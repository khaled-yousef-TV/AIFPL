import pytest

import aifpl.hermes as hermes_module
from aifpl.current_projections import CurrentPlayerProjection
from aifpl.hermes import (
    HermesDecisionBackend,
    HermesManager,
    HermesStrategy,
    HorizonPlanSnapshot,
)
from aifpl.horizon_transfers import PLANNER_VERSION
from aifpl.odds_projections import OddsAdjustedGameweekProjection
from aifpl.optimizer import OptimizedSquad


class FakeModel:
    model_name = "fake-model"

    def __init__(self) -> None:
        self.step = 0

    def complete(self, messages, tools):
        self.step += 1
        if self.step == 1:
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "set_strategy", "arguments": '{"risk_tolerance":0.7,"hit_aversion":0.5,"differential_appetite":0.6,"planning_horizon":4,"preferred_players":["Player 8"],"rationale":"Attack expected points."}'}}]}
        if self.step == 2:
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "2", "type": "function", "function": {"name": "get_initial_squad", "arguments": "{}"}}]}
        return {"role": "assistant", "content": None, "tool_calls": [{"id": "3", "type": "function", "function": {"name": "commit_decision", "arguments": '{"action":"adopt_initial","explanation":"Best backend validated opening squad."}'}}]}


class FakeBackend:
    def context(self):
        return {"source_health": {"overall_status": "healthy"}}

    def initial_squad(self, strategy):
        players = [CurrentPlayerProjection(i, f"Player {i}", "GK" if i <= 2 else "DEF" if i <= 7 else "MID" if i <= 12 else "FWD", chr(65 + i), 50, float(i), 1.0) for i in range(1, 16)]
        return OptimizedSquad(players, 750, 250, 100.0, 1000, "OPTIMAL", "test", players[0:1] + players[2:7] + players[7:12], players[11]), 1, HorizonPlanSnapshot(
            projection_catalog="fake-catalog.json", pre_season=True, solver_status="OPTIMAL",
            methodology="test", planner_version=PLANNER_VERSION,
        )


class FakeHorizonBackend(HermesDecisionBackend):
    def _horizon_rows(self, target_gameweek: int, horizon: int):
        players = [
            (1, "GK", "A", 4), (2, "GK", "B", 3),
            (3, "DEF", "A", 5), (4, "DEF", "B", 4), (5, "DEF", "C", 3),
            (6, "DEF", "D", 2), (7, "DEF", "E", 1),
            (8, "MID", "A", 8), (9, "MID", "B", 7), (10, "MID", "C", 6),
            (11, "MID", "D", 5), (12, "MID", "E", 4),
            (13, "FWD", "F", 9), (14, "FWD", "G", 8), (15, "FWD", "H", 7),
        ]
        return [
            OddsAdjustedGameweekProjection(identifier, f"Player {identifier}", position, club, 50, gameweek, 1, 1, points)
            for gameweek in range(target_gameweek, target_gameweek + horizon)
            for identifier, position, club, points in players
        ], "fake-catalog.json"


def test_hermes_sets_strategy_and_persists_autonomous_decision(tmp_path) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeBackend())

    result = manager.run()

    assert result.decision.action == "adopt_initial"
    assert result.decision.strategy.risk_tolerance == 0.7
    assert result.decision.model == "fake-model"
    assert manager.latest_state().version == 1
    assert manager.latest_decision() == result.decision


def test_latest_decision_found_when_working_directory_differs(tmp_path, monkeypatch) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeBackend())
    result = manager.run()
    monkeypatch.chdir("/tmp")

    assert manager.latest_decision() == result.decision
    assert manager.latest_state().version == 1


def test_initial_squad_uses_empty_squad_horizon_plan(tmp_path, monkeypatch) -> None:
    backend = FakeHorizonBackend(tmp_path)
    strategy = HermesStrategy(
        risk_tolerance=0.6, hit_aversion=0.5, differential_appetite=0.4,
        planning_horizon=3, rationale="Test strategy.",
    )
    captured = {}
    original = hermes_module.plan_horizon_transfers

    def capture_plan(rows, state, **kwargs):
        plan = original(rows, state, **kwargs)
        captured["state"] = state
        captured["pre_season"] = kwargs["pre_season"]
        captured["plan"] = plan
        return plan

    monkeypatch.setattr(hermes_module, "plan_horizon_transfers", capture_plan)

    squad, gameweek, snapshot = backend.initial_squad(strategy)

    opening = captured["plan"].gameweeks[0]
    assert captured["state"].player_ids == []
    assert captured["state"].bank == 0
    assert captured["pre_season"] is True
    assert gameweek == opening.gameweek
    assert squad.players == opening.resulting_squad
    assert squad.bank == opening.bank_after
    assert snapshot.projection_catalog == "fake-catalog.json"
    assert snapshot.pre_season is True
    assert snapshot.weeks[0].gameweek == opening.gameweek


def test_hermes_reinitializes_a_legacy_opening_state_once(tmp_path, monkeypatch) -> None:
    model = FakeModel()
    manager = HermesManager(tmp_path, model=model, backend=FakeBackend())
    initial = manager.run(expected_gameweek=1, expected_season_id="2026-27")
    legacy = manager.latest_state().model_copy(update={"initialization_method": ""})
    monkeypatch.setattr(manager, "latest_state", lambda optional=False: legacy)

    reinitialized = manager.reinitialize_opening_squad(1, "2026-27")

    assert reinitialized is not None
    assert reinitialized.decision.action == "adopt_initial"
    assert model.step == 3  # Reinitialization does not call the LLM.
    reloaded = HermesManager(tmp_path, model=FakeModel(), backend=FakeBackend())
    assert reloaded.latest_state().version == 2
    assert reloaded.latest_state().initialization_method == "horizon_v1"
    assert len(reloaded.decisions()) == 2
    assert reloaded.reinitialize_opening_squad(1, "2026-27") is None
    assert (tmp_path / initial.decision.decision_path).exists()


@pytest.mark.parametrize("action", ("hold", "execute_horizon"))
def test_hermes_gw1_commit_resets_free_transfers(tmp_path, action) -> None:
    backend = FakeHorizonBackend(tmp_path)
    manager = HermesManager(tmp_path, model=FakeModel(), backend=backend)
    manager.run(expected_gameweek=1, expected_season_id="2026-27")
    previous = manager.latest_state()
    manager._strategy = previous.strategy
    manager._horizon, manager._catalog_id = backend.horizon_plan(previous.squad, previous.strategy, 1)
    manager._hold = backend.hold_week(previous.squad, previous.strategy.planning_horizon, 1)
    manager._expected_gameweek = 1
    manager._expected_season_id = "2026-27"

    result = manager._commit({"action": action, "explanation": "Opening-gameweek state check."}, previous)

    assert result.decision.squad.free_transfers == 1


def test_hermes_returns_the_existing_decision_for_a_duplicate_current_gameweek(tmp_path) -> None:
    model = FakeModel()
    manager = HermesManager(tmp_path, model=model, backend=FakeBackend())
    initial = manager.run(expected_gameweek=1, expected_season_id="2026-27")

    repeated = manager.run(expected_gameweek=1, expected_season_id="2026-27")

    assert repeated.decision == initial.decision
    assert repeated.tool_steps == 0
    assert manager.latest_state().version == 1
    assert len(manager.decisions()) == 1
    assert model.step == 3


def test_initial_squad_persists_horizon_plan_snapshot(tmp_path) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeHorizonBackend(tmp_path))

    result = manager.run(expected_gameweek=1, expected_season_id="2026-27")

    snapshot = result.decision.horizon_plan
    assert snapshot is not None
    assert snapshot.projection_catalog == "fake-catalog.json"
    assert snapshot.pre_season is True
    assert len(snapshot.weeks) == 4
    assert snapshot.weeks[0].gameweek == 1
    assert snapshot.weeks[0].free_transfers_before == 5
    assert snapshot.weeks[0].unlimited_transfers is True
    assert snapshot.weeks[0].free_transfers_after == 1
    assert snapshot.weeks[1].free_transfers_before == 1
    assert len(snapshot.weeks[0].squad_ids) == 15
    assert len(snapshot.weeks[0].starting_xi_ids) == 11
    assert snapshot.weeks[0].captain_id is not None
    assert snapshot.weeks[0].robustness_score >= 0
    assert snapshot.robustness_score >= 0
    assert snapshot.total_net_projected_points > 0


def test_strategy_churn_penalty_scales_with_risk_tolerance(monkeypatch) -> None:
    from aifpl.hermes import _strategy_churn_penalty

    monkeypatch.setenv("AIFPL_TRANSFER_PENALTY", "1.0")
    cautious = HermesStrategy(risk_tolerance=0.0, hit_aversion=0.5, differential_appetite=0.0,
                              planning_horizon=3, rationale="test")
    aggressive = HermesStrategy(risk_tolerance=1.0, hit_aversion=0.5, differential_appetite=0.0,
                                planning_horizon=3, rationale="test")

    assert _strategy_churn_penalty(cautious) == 3.0
    assert _strategy_churn_penalty(aggressive) == 1.0


def test_hermes_skips_reinitialization_when_opening_plan_is_current(tmp_path) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeHorizonBackend(tmp_path))
    manager.run(expected_gameweek=1, expected_season_id="2026-27")

    assert manager.reinitialize_opening_squad(1, "2026-27") is None


def test_hermes_force_reinitializes_a_current_opening_squad(tmp_path) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeHorizonBackend(tmp_path))
    manager.run(expected_gameweek=1, expected_season_id="2026-27")

    result = manager.reinitialize_opening_squad(1, "2026-27", force=True)

    assert result is not None
    assert result.decision.action == "adopt_initial"
    reloaded = HermesManager(tmp_path, model=FakeModel(), backend=FakeHorizonBackend(tmp_path))
    assert reloaded.latest_state().version == 2
    assert len(reloaded.decisions()) == 2


def test_hermes_reinitializes_when_planner_version_is_stale(tmp_path, monkeypatch) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeHorizonBackend(tmp_path))
    manager.run(expected_gameweek=1, expected_season_id="2026-27")
    monkeypatch.setattr(hermes_module, "PLANNER_VERSION", "v-next")

    result = manager.reinitialize_opening_squad(1, "2026-27")

    assert result is not None
    assert result.decision.horizon_plan.planner_version == "v-next"
    reloaded = HermesManager(tmp_path, model=FakeModel(), backend=FakeHorizonBackend(tmp_path))
    assert reloaded.latest_state().version == 2
    assert reloaded.latest_decision().horizon_plan.planner_version == "v-next"
    assert reloaded.reinitialize_opening_squad(1, "2026-27") is None


def test_context_includes_scored_decision_history(tmp_path) -> None:
    from aifpl.artifacts import json_bytes, write_immutable
    from aifpl.hermes import HermesDecisionBackend

    record = {
        "decision_path": "d.json", "gameweek": 1, "season_id": "2026-27", "action": "hold",
        "scoring_at": "2026-08-24T10:00:00+00:00", "projection_catalog": "c.json",
        "event_snapshot": "e.json", "xi_projected": 60, "xi_actual": 70,
        "bench_projected": 0, "bench_actual": 2, "captain": None,
        "transfers": [{"out_element": 5, "in_element": 16, "out_name": "A", "in_name": "B",
                       "out_actual": 4, "in_actual": 9, "delta": 5}],
        "players": [], "total_projected": 60, "total_actual": 72,
    }
    write_immutable(tmp_path / "scoring" / "decisions" / "20260824T100000000001Z.json",
                    json_bytes(record, pretty=True))

    context = HermesDecisionBackend(tmp_path).context()

    assert context["decision_history"]["summary"]["scored_gameweeks"] == 1
    assert context["decision_history"]["summary"]["avg_actual_minus_projected"] == 12.0
    assert context["decision_history"]["summary"]["total_transfer_delta"] == 5.0
    assert context["decision_history"]["rows"][0]["gameweek"] == 1


def test_hermes_persists_run_transcript(tmp_path) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeBackend())
    manager.run()

    transcript = manager.latest_transcript()

    assert transcript.outcome == "succeeded"
    assert transcript.tool_steps == 3
    assert transcript.decision_path
    roles = [message["role"] for message in transcript.messages]
    assert roles.count("tool") == 3
    calls = [
        call["function"]["name"]
        for message in transcript.messages
        if message["role"] == "assistant"
        for call in (message.get("tool_calls") or [])
    ]
    assert "set_strategy" in calls and "get_initial_squad" in calls


def test_hermes_decision_history(tmp_path) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeBackend())
    result = manager.run()
    state = manager.latest_state()
    manager.migrate_legacy_state(dict(state.squad.purchase_prices), result.decision.gameweek, state.season_id)

    history = manager.decisions()

    assert len(history) == 2
    assert history[0].created_at >= history[1].created_at
