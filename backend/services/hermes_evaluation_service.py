"""
Hermes learning loop service.

After each gameweek finishes:
1. Score every Hermes run of that GW against actual points (evaluation
   JSON stored on the run).
2. Rebuild the calibration profile (trailing window of scored runs).
3. Decay old lessons and ask the LLM to distill <=3 new ones.

Every future Hermes prompt receives the calibration digest + active
lessons (in-context learning), and the orchestrator shrinks multipliers
by the per-action trust weights (numeric safety net).
"""

import logging
from typing import Dict, List, Optional

from hermes.evaluation import (
    build_calibration_profile,
    calibration_digest,
    evaluate_run,
)

from .dependencies import get_dependencies

logger = logging.getLogger(__name__)

# Trailing window of scored runs feeding the calibration profile
CALIBRATION_WINDOW = 16
MAX_NEW_LESSONS = 3
LESSON_DECAY = 0.9

LESSONS_SYSTEM_PROMPT = """You are reviewing your own past Fantasy Premier League recommendations \
against what actually happened. Given the scored outcomes, write AT MOST 3 short, actionable \
lessons (1-2 sentences each) that would have improved the recommendations. Focus on patterns, \
not one-off variance (a single blank is luck; a repeated miss is a lesson).

Respond with ONLY a JSON object:
{"lessons": [{"category": "captaincy|adjustments|transfers|news|chips", "lesson": str}]}
If outcomes look like normal variance with no pattern, return {"lessons": []}."""


def find_newly_finished_gameweek() -> Optional[int]:
    """
    Return the id of the most recent gameweek that is finished with final
    data, or None.
    """
    deps = get_dependencies()
    finished = [
        gw for gw in deps.fpl_client.get_gameweeks()
        if getattr(gw, "finished", False) and getattr(gw, "data_checked", False)
    ]
    return max((gw.id for gw in finished), default=None)


def evaluate_gameweek(gameweek: int) -> Dict:
    """Evaluate all not-yet-scored Hermes runs for a gameweek."""
    deps = get_dependencies()
    db = deps.db_manager

    runs = [
        r for r in db.get_hermes_runs_for_gameweek(gameweek)
        if r["status"] in ("completed", "degraded") and not r.get("evaluation")
    ]
    if not runs:
        return {"gameweek": gameweek, "evaluated": 0}

    try:
        actual_points = deps.fpl_client.get_event_live(gameweek)
    except Exception as e:
        logger.error(f"Evaluation: failed to fetch GW{gameweek} live points: {e}")
        return {"gameweek": gameweek, "evaluated": 0, "error": str(e)}

    evaluated = 0
    for run in runs:
        try:
            evaluation = evaluate_run(
                adjustments=run.get("adjustments"),
                result=run.get("result"),
                actual_points=actual_points,
                signals=run.get("signals"),
                run_type=run.get("run_type"),
            )
            if db.update_hermes_run(run["run_id"], evaluation=evaluation):
                evaluated += 1
        except Exception as e:
            logger.error(f"Evaluation failed for run {run['run_id']}: {e}", exc_info=True)

    logger.info(f"Evaluated {evaluated} Hermes runs for GW{gameweek}")
    return {"gameweek": gameweek, "evaluated": evaluated}


def get_calibration_profile() -> Dict:
    """Build the calibration profile from the trailing window of scored runs."""
    deps = get_dependencies()
    runs = deps.db_manager.get_evaluated_hermes_runs(limit=CALIBRATION_WINDOW)
    return build_calibration_profile([r["evaluation"] for r in runs if r.get("evaluation")])


def get_memory_digest(profile: Optional[Dict] = None) -> str:
    """
    Calibration + active lessons, formatted for the Hermes prompt.

    Pass `profile` to reuse an already-built calibration profile and avoid a
    second DB query + aggregation when the caller also needs trust weights.
    """
    deps = get_dependencies()
    if profile is None:
        profile = get_calibration_profile()
    lessons = deps.db_manager.get_active_lessons()
    return calibration_digest(profile, lessons)


def generate_lessons(gameweek: int) -> int:
    """LLM pass distilling new lessons from this GW's evaluations. Returns count saved."""
    from hermes.config import load_hermes_config
    from hermes.llm_client import LLMClient
    from hermes.validation import extract_json_block
    import json

    config = load_hermes_config()
    if not config.llm_configured:
        return 0

    deps = get_dependencies()
    db = deps.db_manager

    evaluations = [
        {"run_type": r["run_type"], "evaluation": r["evaluation"]}
        for r in db.get_hermes_runs_for_gameweek(gameweek)
        if r.get("evaluation")
    ]
    if not evaluations:
        return 0

    profile = get_calibration_profile()
    user_prompt = (
        f"# Gameweek {gameweek} scored outcomes\n"
        + json.dumps(evaluations, separators=(",", ":"), default=str)[:6000]
        + "\n\n# Your running calibration\n"
        + json.dumps(profile, separators=(",", ":"))
    )

    try:
        raw, _usage = LLMClient(config).complete(LESSONS_SYSTEM_PROMPT, user_prompt, max_tokens=600)
        data = json.loads(extract_json_block(raw))
    except Exception as e:
        logger.error(f"Lesson generation failed: {e}")
        return 0

    valid_categories = {"captaincy", "adjustments", "transfers", "news", "chips"}
    saved = 0
    for entry in (data.get("lessons") or [])[:MAX_NEW_LESSONS]:
        if not isinstance(entry, dict) or not entry.get("lesson"):
            continue
        category = entry.get("category") if entry.get("category") in valid_categories else "adjustments"
        if db.save_hermes_lesson(gameweek, category, str(entry["lesson"])[:400]):
            saved += 1

    logger.info(f"Saved {saved} new Hermes lessons from GW{gameweek}")
    return saved


def run_learning_cycle() -> Dict:
    """
    Full learning cycle (called by the daily scheduler job):
    detect finished GW -> evaluate runs -> decay lessons -> distill new ones.
    Idempotent: already-evaluated runs are skipped.
    """
    deps = get_dependencies()
    gameweek = find_newly_finished_gameweek()
    if gameweek is None:
        return {"status": "no_finished_gameweek"}

    result = evaluate_gameweek(gameweek)
    if result.get("evaluated", 0) > 0:
        active = deps.db_manager.decay_lessons(factor=LESSON_DECAY)
        new = generate_lessons(gameweek)
        result.update({"lessons_active": active, "lessons_new": new})
    return result
