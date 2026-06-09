"""
Hermes run service.

Starts Hermes runs in background threads (LLM calls are slow — never run
them in request handlers), persists progress to the Task table and
results to the HermesRun table, and serves cached runs per
(run_type, gameweek, day).
"""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .dependencies import get_dependencies

logger = logging.getLogger(__name__)

# One Hermes run at a time per run_type (LLM + agents are heavy)
_running_lock = threading.Lock()
_running_types = set()


def get_hermes_status() -> Dict:
    """Status for frontend gating."""
    from hermes.config import load_hermes_config
    from hermes.search import load_search_provider

    config = load_hermes_config()
    search = load_search_provider()
    return {
        "hermes_enabled": config.enabled,
        "llm_configured": config.llm_configured,
        "model": config.model,
        "daily_briefing": config.daily_briefing,
        "search_provider": search.name,
        "news_agent_enabled": config.llm_configured,  # full mode needs the LLM
    }


def start_hermes_run(
    run_type: str,
    fpl_team_id: Optional[int] = None,
    user_player_ids: Optional[List[int]] = None,
    budget: float = 100.0,
    force: bool = False,
) -> Dict:
    """
    Start a Hermes run in a background thread.

    Returns {task_id, run_id, cached} — when a completed run for the same
    (run_type, gameweek, day) exists and force is False, returns it
    without spawning a new run.
    """
    deps = get_dependencies()
    db = deps.db_manager

    next_gw = deps.fpl_client.get_next_gameweek()
    gameweek = next_gw.id if next_gw else 0

    # Serve today's completed run unless forced
    if not force:
        latest = db.get_latest_hermes_run(
            run_type=run_type, gameweek=gameweek,
            statuses=["completed", "degraded"],
        )
        if latest and latest.get("created_at", "").startswith(
            datetime.now(timezone.utc).date().isoformat()
        ):
            return {"task_id": None, "run_id": latest["run_id"], "cached": True}

    with _running_lock:
        if run_type in _running_types:
            raise RuntimeError(f"A Hermes '{run_type}' run is already in progress")
        _running_types.add(run_type)

    run_id = f"hermes_{run_type}_{uuid.uuid4().hex[:12]}"
    task_id = f"task_{run_id}"

    db.save_hermes_run(run_id, gameweek, run_type, status="pending", fpl_team_id=fpl_team_id)
    db.create_task(
        task_id=task_id,
        task_type="hermes_run",
        title=f"Hermes {run_type} (GW{gameweek})",
        description=f"Hermes orchestrator run: {run_type}",
        status="pending",
    )

    thread = threading.Thread(
        target=_execute_run,
        args=(run_id, task_id, run_type, fpl_team_id, user_player_ids, budget),
        daemon=False,
        name=f"HermesRun-{run_id}",
    )
    thread.start()

    return {"task_id": task_id, "run_id": run_id, "cached": False}


def _execute_run(
    run_id: str,
    task_id: str,
    run_type: str,
    fpl_team_id: Optional[int],
    user_player_ids: Optional[List[int]],
    budget: float,
) -> None:
    """Background thread body: run the orchestrator and persist everything."""
    deps = get_dependencies()
    db = deps.db_manager

    def progress(pct: int, msg: str):
        try:
            db.update_task(task_id, status="running", progress=pct)
        except Exception:
            pass
        logger.info(f"[{run_id}] {pct}% — {msg}")

    try:
        db.update_hermes_run(run_id, status="running")
        db.update_task(task_id, status="running", progress=5)

        from hermes.config import load_hermes_config
        from hermes.orchestrator import HermesOrchestrator

        orchestrator = HermesOrchestrator(load_hermes_config())
        outcome = orchestrator.run(
            run_type=run_type,
            budget=budget,
            user_player_ids=user_player_ids,
            progress_cb=progress,
        )

        db.update_hermes_run(
            run_id,
            status=outcome["status"],
            signals=outcome["signals"],
            adjustments=outcome["adjustments"],
            result=outcome["result"],
            narrative=outcome["narrative"],
            model=outcome.get("model"),
            prompt_tokens=outcome["usage"]["prompt_tokens"],
            completion_tokens=outcome["usage"]["completion_tokens"],
        )
        db.update_task(task_id, status="completed", progress=100)

        # Keep the existing decision audit trail coherent
        try:
            db.log_decision(
                gameweek=outcome["gameweek"],
                decision_type=f"hermes_{run_type}",
                details={"run_id": run_id, "status": outcome["status"]},
                reasoning=(outcome["narrative"] or "")[:2000],
            )
        except Exception as e:
            logger.warning(f"[{run_id}] failed to log decision: {e}")

        logger.info(f"[{run_id}] Hermes run finished: {outcome['status']}")
    except Exception as e:
        logger.error(f"[{run_id}] Hermes run failed: {e}", exc_info=True)
        db.update_hermes_run(run_id, status="failed", error=str(e))
        db.update_task(task_id, status="failed", error=str(e))
    finally:
        with _running_lock:
            _running_types.discard(run_type)
