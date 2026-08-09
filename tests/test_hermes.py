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
