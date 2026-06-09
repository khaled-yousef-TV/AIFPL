"""
Hermes endpoints.

- GET  /signals       — run the deterministic agents, return their reports
- POST /run           — start a Hermes orchestrator run (background thread)
- GET  /latest        — latest run for a run_type (MUST be registered
                        before /runs/{run_id} — see commit f60ad88)
- GET  /runs/{run_id} — fetch a specific run
- GET  /status        — config status for frontend gating
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agents.base import AgentContext
from agents.registry import AGENTS, run_agents
from services.dependencies import get_dependencies

logger = logging.getLogger(__name__)

router = APIRouter()


class HermesRunRequest(BaseModel):
    run_type: str = "briefing"
    fpl_team_id: Optional[int] = None
    budget: float = Field(default=100.0, ge=80.0, le=120.0)
    force: bool = False


async def _resolve_user_player_ids(fpl_team_id: Optional[int]) -> list:
    """Resolve an imported FPL team to its player ids (404s if unknown)."""
    if fpl_team_id is None:
        return []
    from services.fpl_import_service import import_fpl_team
    try:
        imported = await import_fpl_team(fpl_team_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return [p["id"] for p in imported.get("squad", []) if p.get("id")]


def build_agent_context(top_n: int = 40, user_player_ids=None) -> AgentContext:
    """Build an AgentContext from the app's shared dependencies."""
    deps = get_dependencies()
    next_gw = deps.fpl_client.get_next_gameweek()
    return AgentContext(
        fpl_client=deps.fpl_client,
        feature_engineer=deps.feature_engineer,
        predictor=deps.predictor_heuristic,
        betting_odds_client=deps.betting_odds_client,
        db_manager=deps.db_manager,
        gameweek=next_gw.id if next_gw else 0,
        top_n=top_n,
        user_player_ids=user_player_ids or [],
    )


@router.get("/signals")
async def get_signals(
    top_n: int = Query(default=40, ge=10, le=200),
    agents: Optional[str] = Query(
        default=None,
        description="Comma-separated agent names to run (default: all). "
                    f"Available: {', '.join(AGENTS.keys())}",
    ),
    fpl_team_id: Optional[int] = Query(
        default=None,
        description="Imported FPL team id — its players are marked/included in signals",
    ),
):
    """Run the deterministic Hermes agents and return their signal reports."""
    try:
        user_player_ids = await _resolve_user_player_ids(fpl_team_id)

        include = None
        if agents:
            include = [a.strip() for a in agents.split(",") if a.strip()]
            unknown = [a for a in include if a not in AGENTS]
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown agents: {unknown}. Available: {list(AGENTS.keys())}",
                )

        ctx = build_agent_context(top_n=top_n, user_player_ids=user_player_ids)
        reports = run_agents(ctx, include=include)

        return {
            "gameweek": ctx.gameweek,
            "agents_run": list(reports.keys()),
            "reports": {
                name: report.model_dump(mode="json")
                for name, report in reports.items()
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error running Hermes signals: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run")
async def start_run(request: HermesRunRequest):
    """Start a Hermes orchestrator run in the background. Poll the returned task/run."""
    from hermes.orchestrator import RUN_TYPES
    from services.hermes_service import start_hermes_run

    if request.run_type not in RUN_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown run_type '{request.run_type}'. Valid: {RUN_TYPES}",
        )
    if request.run_type == "my_team" and request.fpl_team_id is None:
        raise HTTPException(status_code=400, detail="my_team runs require fpl_team_id")

    try:
        user_player_ids = await _resolve_user_player_ids(request.fpl_team_id)
        outcome = start_hermes_run(
            run_type=request.run_type,
            fpl_team_id=request.fpl_team_id,
            user_player_ids=user_player_ids,
            budget=request.budget,
            force=request.force,
        )
        return outcome
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting Hermes run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# NOTE: /latest must stay registered before /runs/{run_id} (route-order bug, f60ad88)
@router.get("/latest")
async def get_latest_run(
    run_type: Optional[str] = Query(default=None),
    gameweek: Optional[int] = Query(default=None),
):
    """Get the most recent completed/degraded Hermes run."""
    try:
        deps = get_dependencies()
        run = deps.db_manager.get_latest_hermes_run(
            run_type=run_type, gameweek=gameweek,
            statuses=["completed", "degraded"],
        )
        if not run:
            raise HTTPException(status_code=404, detail="No Hermes runs found")
        return run
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching latest Hermes run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """Get a Hermes run by id."""
    try:
        deps = get_dependencies()
        run = deps.db_manager.get_hermes_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Hermes run '{run_id}' not found")
        return run
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching Hermes run {run_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status():
    """Hermes configuration status (for frontend gating)."""
    try:
        from services.hermes_service import get_hermes_status
        return get_hermes_status()
    except Exception as e:
        logger.error(f"Error fetching Hermes status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
