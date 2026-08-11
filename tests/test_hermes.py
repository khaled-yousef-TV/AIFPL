from aifpl.current_projections import CurrentPlayerProjection
from aifpl.hermes import HermesDecisionBackend, HermesManager
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
        return OptimizedSquad(players, 750, 250, 100.0, 1000, "OPTIMAL", "test", players[0:1] + players[2:7] + players[7:12], players[11]), 1


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
