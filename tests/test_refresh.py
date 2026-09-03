import json
import os
from fcntl import LOCK_EX, LOCK_UN, flock

import pytest

from aifpl.fpl import FplSourceError
from aifpl.game_state import GameState, GameStateStore
from aifpl.refresh import CurrentDataRefreshJob, RefreshJobError, _current_hermes_state, _rank_inputs


class FailingFplClient:
    async def fetch_bootstrap(self) -> dict:
        raise FplSourceError("source unavailable")


def test_failed_refresh_job_is_persisted_for_audit(tmp_path) -> None:
    job = CurrentDataRefreshJob(tmp_path, fpl_client=FailingFplClient())

    with pytest.raises(RefreshJobError) as caught:
        job.run(1, 1)

    result = caught.value.result
    persisted = json.loads((tmp_path / result.output_path).read_text(encoding="utf-8"))
    assert result.status == "failed"
    assert result.completed_steps == []
    assert "source unavailable" in result.error
    assert persisted["status"] == "failed"
    assert job.latest() == result


def test_refresh_job_validates_range_before_network_access(tmp_path) -> None:
    with pytest.raises(ValueError, match="gameweek range"):
        CurrentDataRefreshJob(tmp_path, fpl_client=FailingFplClient()).run(2, 1)


def test_concurrent_refresh_is_rejected_and_audited(tmp_path) -> None:
    lock_path = tmp_path / "jobs" / "refresh" / "current.lock"
    lock_path.parent.mkdir(parents=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    flock(descriptor, LOCK_EX)
    try:
        with pytest.raises(RefreshJobError) as caught:
            CurrentDataRefreshJob(tmp_path, fpl_client=FailingFplClient()).run(1, 1)
    finally:
        flock(descriptor, LOCK_UN)
        os.close(descriptor)

    assert "already running" in caught.value.result.error


def test_partial_coverage_soft_degrade_config() -> None:
    from aifpl import config

    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setenv("AIFPL_MIN_ODDS_FIXTURE_COVERAGE", "0.5")
    monkeypatch.setenv("AIFPL_PARTIAL_ODDS_FIXTURE_COVERAGE", "0.8")

    assert config.minimum_odds_fixture_coverage() == 0.5
    assert config.partial_odds_fixture_coverage() == 0.8

    monkeypatch.setenv("AIFPL_MIN_ODDS_FIXTURE_COVERAGE", "0.9")
    try:
        config.partial_odds_fixture_coverage()
    except ValueError:
        pass
    else:
        raise AssertionError("partial threshold below hard floor must raise")
    monkeypatch.undo()


def test_partial_coverage_is_flagged_not_fatal(tmp_path) -> None:
    from aifpl.config import partial_odds_fixture_coverage
    from aifpl.odds_projections import OddsProjectionCatalog

    catalog = OddsProjectionCatalog(
        start_gameweek=1, end_gameweek=6, records=10, output_path="x.jsonl",
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        odds_coverage_by_gameweek={1: 0.6, 2: 0.9, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0},
        odds_coverage_status="partial",
    )

    assert catalog.odds_coverage_status == "partial"
    assert catalog.odds_coverage_by_gameweek[1] < partial_odds_fixture_coverage()


def test_current_hermes_state_reads_the_nested_squad_player_ids(tmp_path, monkeypatch) -> None:
    import aifpl.hermes as hermes_module

    fake_state = type(
        "FakeState", (), {"squad": type("Squad", (), {"player_ids": list(range(1, 16))})(), "season_id": ""}
    )()

    class FakeManager:
        def __init__(self, root) -> None:
            pass

        def latest_state(self, optional=False):
            return fake_state

    monkeypatch.setattr(hermes_module, "HermesManager", FakeManager)
    fake_players = [type("Player", (), {"id": player_id})() for player_id in range(1, 16)]

    state = _current_hermes_state(tmp_path, fake_players)
    assert state is not None
    assert state.squad.player_ids == list(range(1, 16))


def test_refresh_falls_back_to_points_mode_without_usable_rank_state(tmp_path) -> None:
    objective_mode, state, templates = _rank_inputs(tmp_path, None)

    assert objective_mode == "POINTS_MODE"
    assert state is None
    assert templates == {}


def test_refresh_uses_saved_rank_state_when_no_hermes_state_exists(tmp_path) -> None:
    GameStateStore(tmp_path).save(GameState(
        season_id="2026-27", gameweek=3, rank_as_of_gameweek=3, decision_gameweek=3,
        overall_rank=100_000, target_rank=50_000, free_transfers=1, bank=80,
        objective_mode="RANK_MODE",
    ))

    objective_mode, state, templates = _rank_inputs(tmp_path, None)

    assert objective_mode == "RANK_MODE"
    assert state is not None and state.rank_data_available
    assert templates == {}


def test_research_owned_players_derives_season_from_bootstrap(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timezone

    from aifpl.snapshots import SnapshotStore
    from aifpl.tavily_news import TavilyNewsStore
    from aifpl.refresh import _research_owned_players

    bootstrap_payload = {
        "teams": [], "elements": [], "events": [
            {"id": 1, "deadline_time": "2026-08-28T17:30:00Z"},
            {"id": 2, "deadline_time": "2026-09-11T17:30:00Z"},
        ],
    }
    SnapshotStore(tmp_path).save_bootstrap(bootstrap_payload)
    captured = {}

    class FakeStore:
        def __init__(self, root) -> None:
            pass

        def research(self, players, player_ids, gameweek, season_id, query_kind):
            captured["season_id"] = season_id
            captured["player_ids"] = player_ids
            return type("Catalog", (), {"output_path": None, "evidence_records": []})()

    monkeypatch.setattr(TavilyNewsStore, "__init__", lambda self, root: None)
    monkeypatch.setattr(TavilyNewsStore, "research", FakeStore.research)
    fake_state = type("State", (), {
        "squad": type("Squad", (), {"player_ids": list(range(1, 16))})(),
        "season_id": "",
    })()

    _research_owned_players(tmp_path, [], fake_state, 2)

    assert captured["season_id"] == "2026-27"
    assert captured["player_ids"] == list(range(1, 16))
