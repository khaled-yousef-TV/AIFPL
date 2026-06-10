"""
Persistence-layer tests for the Hermes tables (HermesRun, SeasonArchive,
HermesLesson) against a throwaway temp SQLite database.
"""

import os
import tempfile

import pytest

from database.crud import DatabaseManager


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    manager = DatabaseManager(db_url=f"sqlite:///{path}")
    yield manager
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------- HermesRun lifecycle ----------------

def test_hermes_run_create_update_fetch(db):
    assert db.save_hermes_run("run1", gameweek=20, run_type="briefing", status="pending")

    fetched = db.get_hermes_run("run1")
    assert fetched["status"] == "pending"
    assert fetched["completed_at"] is None

    db.update_hermes_run(
        "run1", status="completed", narrative="done",
        adjustments={"captain_ranking": [1]}, prompt_tokens=100,
    )
    updated = db.get_hermes_run("run1")
    assert updated["status"] == "completed"
    assert updated["narrative"] == "done"
    assert updated["adjustments"]["captain_ranking"] == [1]
    assert updated["completed_at"] is not None     # terminal status stamps completed_at


def test_update_only_allows_whitelisted_fields(db):
    db.save_hermes_run("run2", 20, "squad")
    # attempt to set a non-existent / non-allowed field is ignored, not crashed
    db.update_hermes_run("run2", status="running", bogus_field="x")
    assert db.get_hermes_run("run2")["status"] == "running"


def test_get_latest_hermes_run_filters(db):
    db.save_hermes_run("a", 20, "briefing", status="completed")
    db.save_hermes_run("b", 21, "wildcard", status="completed")
    db.update_hermes_run("a", status="completed")
    db.update_hermes_run("b", status="completed")

    latest_wc = db.get_latest_hermes_run(run_type="wildcard", statuses=["completed"])
    assert latest_wc["run_id"] == "b"
    assert db.get_latest_hermes_run(run_type="free_hit") is None


def test_evaluated_runs_query(db):
    db.save_hermes_run("e1", 20, "briefing", status="completed")
    db.update_hermes_run("e1", status="completed", evaluation={"captaincy": {"regret": 2}})
    db.save_hermes_run("e2", 20, "briefing", status="completed")  # no evaluation
    evaluated = db.get_evaluated_hermes_runs()
    assert [r["run_id"] for r in evaluated] == ["e1"]


# ---------------- SeasonArchive ----------------

def test_season_archive_upsert_and_fetch(db):
    entry = {
        "player_id": 1, "player_name": "Salah", "full_name": "Mohamed Salah",
        "team_short": "LIV", "position_id": 3, "total_points": 250,
        "minutes": 3000, "goals": 25, "assists": 18, "points_per_game": 7.5,
        "end_price": 13.0, "ownership": 55.0, "xGI": 30.0, "xGC": 0.0,
        "gw_history": [{"round": 1, "total_points": 8}], "variability": {"mean_pts": 7.5},
    }
    db.save_season_archive_entry("2025-26", entry)
    # upsert: same player again updates rather than duplicating
    db.save_season_archive_entry("2025-26", {**entry, "total_points": 260})

    rows = db.get_season_archive("2025-26")
    assert len(rows) == 1
    assert rows[0]["total_points"] == 260
    assert rows[0]["variability"]["mean_pts"] == 7.5

    seasons = db.get_archived_seasons()
    assert {"season": "2025-26", "players": 1} in seasons


# ---------------- HermesLesson ----------------

def test_lessons_save_fetch_and_decay(db):
    db.save_hermes_lesson(20, "captaincy", "Prefer mean over ceiling.")
    db.save_hermes_lesson(20, "news", "Weight incentives in DGWs.")

    active = db.get_active_lessons()
    assert len(active) == 2
    assert all(l["weight"] == 1.0 for l in active)

    # decay several times until weights fall below the deactivation threshold
    for _ in range(12):
        db.decay_lessons(factor=0.9, deactivate_below=0.3)
    remaining = db.get_active_lessons()
    assert len(remaining) == 0   # 0.9^12 ~= 0.28 < 0.3 -> deactivated
