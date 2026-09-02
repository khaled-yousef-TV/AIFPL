import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import aifpl.hermes as hermes_module
from aifpl.current_projections import CurrentPlayerProjection
from aifpl.hermes import (
    HermesDecisionBackend,
    HermesDecision,
    HermesManager,
    HermesSquadState,
    HermesState,
    HermesStrategy,
    HorizonPlanSnapshot,
)
from aifpl.horizon_transfers import HorizonGameweekPlan, HorizonTransferPlan, PLANNER_VERSION
from aifpl.odds_projections import OddsAdjustedGameweekProjection
from aifpl.optimizer import OptimizedSquad
from aifpl.game_state import GameState


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


class RankFakeModel(FakeModel):
    def complete(self, messages, tools):
        message = super().complete(messages, tools)
        if self.step == 1:
            call = message["tool_calls"][0]
            arguments = json.loads(call["function"]["arguments"])
            arguments["objective_mode"] = "RANK_MODE"
            call["function"]["arguments"] = json.dumps(arguments)
        return message


class FakeBackend:
    def context(self):
        return {"source_health": {"overall_status": "healthy"}}

    def initial_squad(self, strategy):
        players = [CurrentPlayerProjection(i, f"Player {i}", "GK" if i <= 2 else "DEF" if i <= 7 else "MID" if i <= 12 else "FWD", chr(65 + i), 50, float(i), 1.0) for i in range(1, 16)]
        return OptimizedSquad(players, 750, 250, 100.0, 1000, "OPTIMAL", "test", players[0:1] + players[2:7] + players[7:12], players[11]), 1, HorizonPlanSnapshot(
            projection_catalog="fake-catalog.json", pre_season=True, solver_status="OPTIMAL",
            methodology="test", planner_version=PLANNER_VERSION,
        )


class RankAwareFakeBackend(FakeBackend):
    def __init__(self, state: GameState) -> None:
        self.state = state

    def latest_game_state(self):
        return self.state


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


def manual_state_and_plan() -> tuple[HermesState, HorizonTransferPlan, dict[str, object]]:
    players = [
        CurrentPlayerProjection(
            i, f"Player {i}",
            "GK" if i <= 2 else "DEF" if i <= 7 else "MID" if i <= 12 else "FWD",
            chr(64 + i), 50, float(i), 1.0, "test",
        )
        for i in range(1, 16)
    ]
    week = HorizonGameweekPlan(
        gameweek=2, outgoing=[players[0]], incoming=[players[1]], resulting_squad=players,
        starting_xi=players[:11], captain=players[10], transfers_made=1,
        free_transfers_before=1, hit_cost=4, bank_after=20, projected_points=120.0,
        net_projected_points=116.0, odds_coverage=1.0, vice_captain=players[9],
    )
    future = HorizonGameweekPlan(
        gameweek=3, outgoing=[], incoming=[], resulting_squad=players,
        starting_xi=players[:11], captain=players[10], transfers_made=0,
        free_transfers_before=2, hit_cost=0, bank_after=20, projected_points=120.0,
        net_projected_points=120.0, odds_coverage=1.0, vice_captain=players[9],
    )
    plan = HorizonTransferPlan(
        gameweeks=[week, future], total_projected_points=240.0, total_hit_cost=4,
        total_net_projected_points=236.0, solver_status="OPTIMAL", methodology="test",
    )
    strategy = HermesStrategy(
        risk_tolerance=0.5, hit_aversion=0.5, differential_appetite=0.2,
        planning_horizon=3, rationale="test",
    )
    squad = HermesSquadState(
        player_ids=list(range(1, 16)), bank=20, free_transfers=1,
        purchase_prices={player_id: 50 for player_id in range(1, 16)},
    )
    state = HermesState(
        strategy=strategy, squad=squad, captain_id=1, starting_xi_ids=list(range(1, 12)),
        model="test", updated_at=datetime(2026, 8, 14, tzinfo=timezone.utc), version=1,
        gameweek=1, season_id="2026-27", vice_captain_id=2,
    )
    hold = {
        "gameweek": 2, "starting_ids": list(range(2, 13)), "captain_id": 12,
        "vice_captain_id": 11, "projected_points": 99.5, "methodology": "test",
    }
    return state, plan, hold


def test_hermes_sets_strategy_and_persists_autonomous_decision(tmp_path) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeBackend())

    result = manager.run()

    assert result.decision.action == "adopt_initial"
    assert result.decision.strategy.risk_tolerance == 0.7
    assert result.decision.model == "fake-model"
    assert manager.latest_state().version == 1
    assert manager.latest_decision() == result.decision


def test_explicit_objective_mode_allows_a_deliberate_points_to_rank_switch(tmp_path) -> None:
    previous, _, _ = manual_state_and_plan()
    manager = HermesManager(tmp_path)
    manager._requested_objective_mode = "RANK_MODE"

    output = manager._call_tool(
        "set_strategy",
        {
            "risk_tolerance": 0.7,
            "hit_aversion": 0.5,
            "differential_appetite": 0.2,
            "planning_horizon": 3,
            "preferred_players": [],
            "objective_mode": "RANK_MODE",
            "rationale": "Use explicit rank objective for this planning cycle.",
        },
        previous,
    )

    assert output["accepted"] is True
    assert manager._strategy is not None
    assert manager._strategy.objective_mode == "RANK_MODE"


def test_hermes_automatically_uses_rank_mode_from_account_state(tmp_path) -> None:
    state = GameState(
        season_id="2026-27",
        gameweek=1,
        overall_rank=100_000,
        target_rank=50_000,
        free_transfers=1,
        bank=0,
        objective_mode="RANK_MODE",
    )
    manager = HermesManager(
        tmp_path,
        model=RankFakeModel(),
        backend=RankAwareFakeBackend(state),
    )

    result = manager.run(expected_gameweek=1, expected_season_id="2026-27")

    assert result.decision.strategy.objective_mode == "RANK_MODE"


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


def test_committed_hold_rewrites_the_first_horizon_week(tmp_path) -> None:
    state, plan, hold = manual_state_and_plan()
    manager = HermesManager(tmp_path)
    manager._strategy = state.strategy
    manager._horizon = plan
    manager._hold = hold
    manager._catalog_id = "fake-catalog.json"
    manager._expected_gameweek = 2
    manager._expected_season_id = "2026-27"

    result = manager._commit({"action": "hold", "explanation": "Keep the squad."}, state)

    first = result.decision.horizon_plan.weeks[0]
    assert first.transfers_made == 0
    assert first.hit_cost == 0
    assert first.outgoing_ids == []
    assert first.incoming_ids == []
    assert first.squad_ids == state.squad.player_ids
    assert first.starting_xi_ids == hold["starting_ids"]
    assert first.captain_id == hold["captain_id"]
    assert result.decision.transfers_out == []
    assert result.decision.transfers_in == []
    assert result.decision.squad.player_ids == state.squad.player_ids


def test_strategy_change_is_rejected_after_horizon_plan(tmp_path) -> None:
    state, plan, _ = manual_state_and_plan()
    manager = HermesManager(tmp_path)
    manager._strategy = state.strategy
    manager._horizon = plan
    changed = state.strategy.model_copy(update={"risk_tolerance": 0.9})

    with pytest.raises(ValueError, match="cannot change after a plan"):
        manager._call_tool("set_strategy", changed.model_dump(), state)


def test_hermes_reads_ignore_orphan_decisions_and_filter_by_scope(tmp_path) -> None:
    from aifpl.artifacts import json_bytes, write_immutable

    def save_pair(stamp: str, season_id: str, gameweek: int) -> HermesDecision:
        decision_path = tmp_path / "hermes" / "decisions" / f"{stamp}.json"
        state_path = tmp_path / "hermes" / "states" / f"{stamp}.json"
        squad = HermesSquadState(
            player_ids=list(range(1, 16)), bank=20, free_transfers=1,
            purchase_prices={player_id: 50 for player_id in range(1, 16)},
        )
        record = HermesDecision(
            action="hold", gameweek=gameweek, squad=squad, captain_id=1,
            starting_xi_ids=list(range(1, 12)), transfers_out=[], transfers_in=[],
            explanation="test", strategy=HermesStrategy(
                risk_tolerance=0.5, hit_aversion=0.5, differential_appetite=0.2,
                planning_horizon=3, rationale="test",
            ), model="test", created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            backend_methodology="test", decision_path=str(decision_path),
            state_path=str(state_path), season_id=season_id,
        )
        state = HermesState(
            strategy=record.strategy, squad=squad, captain_id=1,
            starting_xi_ids=list(range(1, 12)), model="test",
            updated_at=record.created_at, version=1, gameweek=gameweek,
            season_id=season_id, decision_path=str(decision_path),
        )
        write_immutable(state_path, json_bytes(state.model_dump(mode="json"), pretty=True))
        write_immutable(decision_path, json_bytes(record.model_dump(mode="json"), pretty=True))
        return record

    first = save_pair("20260801T000000000001Z", "2026-27", 1)
    second = save_pair("20260802T000000000002Z", "2026-27", 2)
    orphan = first.model_copy(update={
        "decision_path": str(tmp_path / "hermes" / "decisions" / "20260803T000000000003Z.json"),
        "state_path": str(tmp_path / "hermes" / "states" / "missing.json"),
    })
    write_immutable(
        Path(orphan.decision_path), json_bytes(orphan.model_dump(mode="json"), pretty=True),
    )

    manager = HermesManager(tmp_path)

    assert manager.latest_decision() == second
    assert manager.decisions(season_id="2026-27", gameweek=1) == [first]
    assert manager.decisions(season_id="2025-26") == []


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


def test_hermes_supersedes_gw2_from_a_valid_gw1_state(tmp_path) -> None:
    backend = FakeHorizonBackend(tmp_path)
    manager = HermesManager(tmp_path, model=FakeModel(), backend=backend)
    gw1 = manager.run(expected_gameweek=1, expected_season_id="2026-27")
    base_state = manager.latest_state()
    manager._strategy = base_state.strategy
    manager._horizon, manager._catalog_id = backend.horizon_plan(base_state.squad, base_state.strategy, 2)
    manager._hold = backend.hold_week(base_state.squad, base_state.strategy.planning_horizon, 2)
    manager._expected_gameweek = 2
    manager._expected_season_id = "2026-27"
    bad = manager._commit(
        {"action": "execute_horizon", "explanation": "Invalid full-squad recommendation."},
        base_state,
    )
    bad_bytes = Path(bad.decision.decision_path).read_bytes()

    corrected = manager.supersede_decision(
        Path(gw1.state_path).name,
        Path(bad.decision.decision_path).name,
        "Normal-week transfer limit was missing.",
        expected_gameweek=2,
        expected_season_id="2026-27",
    )

    assert corrected.decision.gameweek == 2
    assert corrected.decision.base_state_path == gw1.state_path
    assert corrected.decision.supersedes_decision_path == bad.decision.decision_path
    assert corrected.decision.correction_reason == "Normal-week transfer limit was missing."
    assert manager.latest_decision() == corrected.decision
    assert manager.latest_state().version == 3
    assert Path(bad.decision.decision_path).read_bytes() == bad_bytes
    receipt = json.loads(Path(corrected.correction_path).read_text(encoding="utf-8"))
    assert receipt["replacement_decision_path"] == corrected.decision.decision_path


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
    assert snapshot.weeks[0].vice_captain_id is not None
    assert snapshot.weeks[0].vice_captain_id != snapshot.weeks[0].captain_id
    assert snapshot.weeks[0].robustness_score >= 0
    assert snapshot.robustness_score >= 0
    assert snapshot.total_net_projected_points > 0


def test_hermes_decision_records_the_vice_captain(tmp_path) -> None:
    manager = HermesManager(tmp_path, model=FakeModel(), backend=FakeHorizonBackend(tmp_path))

    result = manager.run(expected_gameweek=1, expected_season_id="2026-27")

    assert result.decision.vice_captain_id is not None
    assert result.decision.vice_captain_id != result.decision.captain_id
    assert manager.latest_state().vice_captain_id == result.decision.vice_captain_id


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
