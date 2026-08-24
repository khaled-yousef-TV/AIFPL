from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from aifpl.artifacts import json_bytes, jsonl_bytes, write_immutable, write_manifest
from aifpl.hermes import HermesDecision, HermesSquadState, HermesStrategy
from aifpl.notifier import build_scorecard_message
from aifpl.odds_projections import OddsAdjustedGameweekProjection
from aifpl.scoring import CompletedDecisionScorer, DecisionScorer
from aifpl.snapshots import SnapshotStore

SQUAD_IDS = list(range(1, 16))


def strategy() -> HermesStrategy:
    return HermesStrategy(
        risk_tolerance=0.4, hit_aversion=0.8, differential_appetite=0.3,
        planning_horizon=4, preferred_players=[], rationale="test",
    )


def write_decision(tmp_path: Path) -> Path:
    stamp = "20260809T000000000001Z"
    decision_path = tmp_path / f"decision_{stamp}.json"
    squad = HermesSquadState(
        player_ids=SQUAD_IDS, bank=20, free_transfers=1,
        purchase_prices={element: 50 for element in SQUAD_IDS},
    )
    decision = HermesDecision(
        action="hold", gameweek=1, squad=squad, captain_id=1, starting_xi_ids=list(range(1, 12)),
        transfers_out=[5], transfers_in=[16], explanation="test",
        strategy=strategy(), model="test-model", created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        backend_methodology="test", decision_path=str(decision_path),
        state_path=str(tmp_path / "state.json"), season_id="2026-27",
    )
    write_immutable(decision_path, json_bytes(decision.model_dump(mode="json"), pretty=True))
    return decision_path


def write_committed_decision(tmp_path: Path) -> Path:
    stamp = "20260809T000000000001Z"
    decision_path = tmp_path / "hermes" / "decisions" / f"{stamp}.json"
    state_path = tmp_path / "hermes" / "states" / f"{stamp}.json"
    squad = HermesSquadState(
        player_ids=SQUAD_IDS, bank=20, free_transfers=1,
        purchase_prices={element: 50 for element in SQUAD_IDS},
    )
    decision = HermesDecision(
        action="hold", gameweek=1, squad=squad, captain_id=1, starting_xi_ids=list(range(1, 12)),
        transfers_out=[5], transfers_in=[16], explanation="test",
        strategy=strategy(), model="test-model", created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        backend_methodology="test", decision_path=str(decision_path), state_path=str(state_path), season_id="2026-27",
    )
    write_immutable(state_path, json_bytes({}, pretty=True))
    write_immutable(decision_path, json_bytes(decision.model_dump(mode="json"), pretty=True))
    return decision_path


def write_catalog(tmp_path: Path) -> None:
    rows = [
        OddsAdjustedGameweekProjection(
            identifier, f"Player {identifier}", "GK" if identifier <= 2 else "DEF" if identifier <= 7 else "MID" if identifier <= 12 else "FWD",
            chr(64 + identifier), 50, 1, 1, 1, float(identifier),
        )
        for identifier in SQUAD_IDS + [16]
    ]
    directory = tmp_path / "normalized" / "current" / "odds_projections"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "gw1-1.20260809T000000000000Z.test.jsonl"
    write_immutable(output, jsonl_bytes(rows))
    write_manifest(tmp_path, output, artifact_type="odds_projections", created_at="2026-08-09T00:00:00Z",
                   record_count=len(rows), sources={})


def write_event_results(tmp_path: Path) -> None:
    elements = [{"id": identifier, "stats": {"total_points": identifier + 1}} for identifier in SQUAD_IDS + [16]]
    SnapshotStore(tmp_path).save_event_live(1, {"elements": elements}, datetime(2026, 8, 23, tzinfo=timezone.utc))


def test_score_decision_compares_projections_to_actuals(tmp_path) -> None:
    write_catalog(tmp_path)
    write_event_results(tmp_path)
    decision_path = write_decision(tmp_path)

    record = DecisionScorer(tmp_path).score(decision_path)

    assert record.gameweek == 1
    assert record.total_projected == 121.0
    assert record.total_actual == 137.0
    assert record.captain is not None
    assert record.captain.actual == 2.0
    assert len(record.transfers) == 1
    assert record.transfers[0].delta == 11.0
    assert (tmp_path / "scoring" / "decisions").exists()
    assert DecisionScorer(tmp_path).latest() == record


def test_score_requires_event_results(tmp_path) -> None:
    write_catalog(tmp_path)
    decision_path = write_decision(tmp_path)

    try:
        DecisionScorer(tmp_path).score(decision_path)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected a missing event snapshot error")


def test_scorecard_message_formats(tmp_path) -> None:
    write_catalog(tmp_path)
    write_event_results(tmp_path)
    DecisionScorer(tmp_path).score(write_decision(tmp_path))

    message = build_scorecard_message(tmp_path)

    assert "GW 1 scorecard" in message
    assert "Projected: 121.0 | Actual: 137.0" in message
    assert "Captain:" in message
    assert "Best performers:" in message


def test_recent_returns_newest_scores_first(tmp_path) -> None:
    from aifpl.artifacts import json_bytes, write_immutable

    for day, stamp in ((1, "20260801T000000000001Z"), (2, "20260802T000000000002Z"), (3, "20260803T000000000003Z")):
        path = tmp_path / "scoring" / "decisions" / f"{stamp}.json"
        record = {
            "decision_path": "d.json", "gameweek": 1, "season_id": "2026-27", "action": "hold",
            "scoring_at": f"2026-08-{day:02d}T00:00:00+00:00",
            "projection_catalog": "c.json", "event_snapshot": "e.json",
            "xi_projected": 1, "xi_actual": 2, "bench_projected": 0, "bench_actual": 0,
            "captain": None, "transfers": [], "players": [],
            "total_projected": 1, "total_actual": 2,
        }
        write_immutable(path, json_bytes(record, pretty=True))

    recent = DecisionScorer(tmp_path).recent(2)

    assert len(recent) == 2
    assert recent[0].scoring_at > recent[1].scoring_at


def test_completed_scorer_persists_one_final_scorecard(tmp_path) -> None:
    class FakeFplClient:
        def __init__(self) -> None:
            self.bootstrap_calls = 0
            self.event_calls: list[int] = []

        async def fetch_bootstrap(self) -> dict:
            self.bootstrap_calls += 1
            return {
                "elements": [], "teams": [],
                "events": [{"id": 1, "deadline_time": "2026-08-14T18:00:00Z", "finished": True}],
            }

        async def fetch_event_live(self, event: int) -> dict:
            self.event_calls.append(event)
            return {"elements": [{"id": identifier, "stats": {"total_points": identifier + 1}} for identifier in SQUAD_IDS + [16]]}

    write_catalog(tmp_path)
    decision_path = write_committed_decision(tmp_path)
    client = FakeFplClient()
    completed = CompletedDecisionScorer(tmp_path, client)

    first = completed.score_pending("2026-27")
    second = completed.score_pending("2026-27")

    assert len(first) == 1
    assert second == []
    assert client.bootstrap_calls == 1
    assert client.event_calls == [1]
    assert DecisionScorer(tmp_path).latest().decision_path == str(decision_path)
    assert DecisionScorer(tmp_path).is_scored(decision_path, 1)
    from aifpl.hermes import HermesDecisionBackend

    assert HermesDecisionBackend(tmp_path).context()["decision_history"]["summary"]["scored_gameweeks"] == 1


def test_completed_scorer_waits_for_fpl_to_finalize_the_gameweek(tmp_path) -> None:
    class FakeFplClient:
        def __init__(self) -> None:
            self.event_calls: list[int] = []

        async def fetch_bootstrap(self) -> dict:
            return {
                "elements": [], "teams": [],
                "events": [{"id": 1, "deadline_time": "2026-08-14T18:00:00Z", "finished": False}],
            }

        async def fetch_event_live(self, event: int) -> dict:
            self.event_calls.append(event)
            return {"elements": []}

    client = FakeFplClient()
    write_committed_decision(tmp_path)

    assert CompletedDecisionScorer(tmp_path, client).score_pending("2026-27") == []
    assert client.event_calls == []
