"""
Season archive service.

Snapshots every relevant player's full per-GW history + summary stats
into the SeasonArchive table BEFORE the FPL API resets for the new
season. The archive serves two purposes:

1. Cold-start prior: the first ~4 GWs of a new season blend last
   season's stats into form/variability/predictions.
2. Backtesting: replaying past gameweeks against what actually happened.
"""

import logging
import threading
import uuid
from typing import Dict, Optional

from data.european_teams import get_current_season

from .dependencies import get_dependencies

logger = logging.getLogger(__name__)

# Archive players with at least this many minutes (skip never-played fringe)
MIN_MINUTES = 90

_archive_lock = threading.Lock()
_archive_running = False


def archive_current_season(progress_cb=None) -> Dict:
    """
    Archive the current season's player data. Synchronous; call from a
    background thread (one element-summary HTTP call per player).
    """
    from agents.variability_agent import compute_variability_stats

    deps = get_dependencies()
    client = deps.fpl_client
    db = deps.db_manager

    season = get_current_season()
    players = client.get_players()
    teams = {t.id: t.short_name for t in client.get_teams()}

    eligible = [p for p in players if p.minutes >= MIN_MINUTES]
    logger.info(f"Archiving season {season}: {len(eligible)}/{len(players)} players (>= {MIN_MINUTES} min)")

    archived = 0
    errors = 0
    for i, p in enumerate(eligible):
        try:
            details = client.get_player_details(p.id)
            history = details.get("history", [])
        except Exception as e:
            errors += 1
            logger.warning(f"Archive: failed history fetch for {p.web_name}: {e}")
            history = []

        points = [h.get("total_points", 0) for h in history if h.get("minutes", 0) > 0]
        variability = compute_variability_stats(points)

        entry = {
            "player_id": p.id,
            "player_name": p.web_name,
            "full_name": p.full_name,
            "team_short": teams.get(p.team, "???"),
            "position_id": p.element_type,
            "total_points": p.total_points,
            "minutes": p.minutes,
            "goals": p.goals_scored,
            "assists": p.assists,
            "points_per_game": float(p.points_per_game),
            "end_price": p.price,
            "ownership": float(p.selected_by_percent),
            "xGI": float(p.expected_goal_involvements),
            "xGC": float(p.expected_goals_conceded),
            "gw_history": history,
            "variability": variability,
        }
        if db.save_season_archive_entry(season, entry):
            archived += 1

        if progress_cb and (i + 1) % 25 == 0:
            progress_cb(int((i + 1) / len(eligible) * 100), f"{i + 1}/{len(eligible)} players")

    result = {"season": season, "archived": archived, "eligible": len(eligible), "errors": errors}
    logger.info(f"Season archive complete: {result}")
    return result


def start_archive_run() -> Dict:
    """Start the archive in a background thread (Task-tracked)."""
    global _archive_running
    deps = get_dependencies()
    db = deps.db_manager

    with _archive_lock:
        if _archive_running:
            raise RuntimeError("A season archive run is already in progress")
        _archive_running = True

    task_id = f"season_archive_{uuid.uuid4().hex[:10]}"
    season = get_current_season()
    db.create_task(
        task_id=task_id,
        task_type="season_archive",
        title=f"Archive season {season}",
        description="Snapshot per-player GW history before the API resets",
        status="pending",
    )

    def run():
        global _archive_running
        try:
            db.update_task(task_id, status="running", progress=1)
            result = archive_current_season(
                progress_cb=lambda pct, msg: db.update_task(task_id, status="running", progress=pct)
            )
            db.update_task(task_id, status="completed", progress=100)
            logger.info(f"[{task_id}] {result}")
        except Exception as e:
            logger.error(f"[{task_id}] archive failed: {e}", exc_info=True)
            db.update_task(task_id, status="failed", error=str(e))
        finally:
            with _archive_lock:
                _archive_running = False

    thread = threading.Thread(target=run, daemon=False, name=f"SeasonArchive-{task_id}")
    thread.start()
    return {"task_id": task_id, "season": season}


# ==================== Cold-start prior access ====================

def get_previous_season(current: Optional[str] = None) -> str:
    """'2026-27' -> '2025-26'."""
    season = current or get_current_season()
    start = int(season.split("-")[0])
    return f"{start - 1}-{str(start)[-2:]}"


def load_prior_by_name() -> Dict[str, Dict]:
    """
    Load last season's archive keyed by lowercase full_name AND web_name.

    Player ids change between seasons, so cross-season matching is by name.
    Returns {} when no archive exists (graceful cold-start without prior).
    """
    deps = get_dependencies()
    db = deps.db_manager

    season = get_current_season()
    prev = get_previous_season(season)
    rows = db.get_season_archive(prev)
    if not rows:
        return {}

    by_name: Dict[str, Dict] = {}
    for r in rows:
        if r.get("full_name"):
            by_name[r["full_name"].lower()] = r
        by_name.setdefault((r.get("player_name") or "").lower(), r)
    return by_name
