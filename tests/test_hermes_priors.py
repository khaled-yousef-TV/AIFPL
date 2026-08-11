import json
from datetime import datetime, timezone

import pytest

import aifpl.hermes as hermes_module
from aifpl.current import CurrentPlayerCatalogStore
from aifpl.hermes import HermesDecisionBackend, HermesManager, HermesStrategy
from aifpl.odds_projections import OddsAdjustedGameweekProjection
from aifpl.priors import PlayerPrior, apply_priors, validate_prior_adjustment
from aifpl.snapshots import SnapshotStore


def projection_row(identifier: int, position: str, club: str, gameweek: int, points: float) -> OddsAdjustedGameweekProjection:
    return OddsAdjustedGameweekProjection(
        identifier, f"Player {identifier}", position, club, 50, gameweek, 1, 1, points,
    )


def synthetic_rows() -> list[OddsAdjustedGameweekProjection]:
    players = [
        (1, "GK", "A", 4), (2, "GK", "B", 3),
        (3, "DEF", "A", 5), (4, "DEF", "B", 4), (5, "DEF", "C", 3), (6, "DEF", "D", 2), (7, "DEF", "E", 1),
        (8, "MID", "A", 8), (9, "MID", "B", 7), (10, "MID", "C", 6), (11, "MID", "D", 5), (12, "MID", "E", 4),
        (13, "FWD", "F", 9), (14, "FWD", "G", 8), (15, "FWD", "H", 7), (16, "MID", "I", 9),
    ]
    return [projection_row(identifier, position, club, gameweek, points) for gameweek in (1, 2, 3) for identifier, position, club, points in players]


def make_catalog(root) -> None:
    elements = []
    for identifier, position, club, _ in [
        (1, "GK", "A", 4), (2, "GK", "B", 3), (3, "DEF", "A", 5), (4, "DEF", "B", 4),
        (5, "DEF", "C", 3), (6, "DEF", "D", 2), (7, "DEF", "E", 1), (8, "MID", "A", 8),
        (9, "MID", "B", 7), (10, "MID", "C", 6), (11, "MID", "D", 5), (12, "MID", "E", 4),
        (13, "FWD", "F", 9), (14, "FWD", "G", 8), (15, "FWD", "H", 7), (16, "MID", "I", 9),
    ]:
        elements.append({
            "id": identifier, "web_name": f"Player {identifier}", "first_name": "First",
            "second_name": f"Player {identifier}", "element_type": {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}[position],
            "team": ord(club) - 64, "now_cost": 50, "status": "a",
            "chance_of_playing_next_round": 100, "form": "0", "points_per_game": "4",
            "total_points": 100, "minutes": 900, "starts": 10,
            "expected_goals": "0", "expected_assists": "0", "expected_goal_involvements": "0",
            "expected_goals_conceded": "10", "news": "", "news_added": None,
            "selected_by_percent": "10", "ep_next": "3",
        })
    teams = [{"id": index, "name": f"Club {chr(64 + index)}", "short_name": chr(64 + index), "code": index} for index in range(1, 10)]
    payload = {"elements": elements, "teams": teams, "events": []}
    path, _ = SnapshotStore(root).save_bootstrap(payload, datetime(2026, 8, 1, tzinfo=timezone.utc))
    CurrentPlayerCatalogStore(root).normalize(path)


class FakePriorsBackend(HermesDecisionBackend):
    def _horizon_rows(self, target_gameweek: int, horizon: int):
        rows = synthetic_rows()
        return [row for row in rows if target_gameweek <= row.gameweek <= target_gameweek + horizon - 1]


class FakePriorsModel:
    model_name = "fake-priors-model"

    def __init__(self) -> None:
        self.step = 0

    def complete(self, messages, tools):
        self.step += 1
        if self.step == 1:
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "set_strategy", "arguments": '{"risk_tolerance":0.6,"hit_aversion":0.5,"differential_appetite":0.4,"planning_horizon":4,"preferred_players":[],"rationale":"Test strategy."}'}}]}
        if self.step == 2:
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "2", "type": "function", "function": {"name": "set_player_prior", "arguments": '{"player_id": 8, "adjustment": -1.5, "reason": "Rotation risk flagged in recent press conference."}'}}]}
        if self.step == 3:
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "3", "type": "function", "function": {"name": "get_initial_squad", "arguments": "{}"}}]}
        return {"role": "assistant", "content": None, "tool_calls": [{"id": "4", "type": "function", "function": {"name": "commit_decision", "arguments": '{"action":"adopt_initial","explanation":"Adopt the adjusted initial squad."}'}}]}


def test_validate_prior_adjustment_bounds() -> None:
    assert validate_prior_adjustment(-2.0) == -2.0
    assert validate_prior_adjustment(1.25) == 1.25
    with pytest.raises(ValueError):
        validate_prior_adjustment(2.01)
    with pytest.raises(ValueError):
        validate_prior_adjustment(-2.5)


def test_apply_priors_adjusts_matching_rows_only() -> None:
    rows = synthetic_rows()[:2]
    prior = PlayerPrior(player_id=1, player_name="Player 1", adjustment=1.0, reason="Strong form narrative.")
    adjusted = apply_priors(rows, [prior])

    assert adjusted[0].projected_points == rows[0].projected_points + 1.0
    assert adjusted[1].projected_points == rows[1].projected_points
    assert rows[0].projected_points == 4  # source untouched


def test_validate_prior_rejects_unknown_player(tmp_path) -> None:
    make_catalog(tmp_path)
    backend = HermesDecisionBackend(tmp_path)

    with pytest.raises(ValueError, match="Unknown player"):
        backend.validate_prior({"player_id": 999, "adjustment": 0.5, "reason": "Unknown player should fail."})


def test_validate_prior_rejects_short_reason(tmp_path) -> None:
    make_catalog(tmp_path)
    backend = HermesDecisionBackend(tmp_path)

    with pytest.raises(ValueError, match="at least 10"):
        backend.validate_prior({"player_id": 8, "adjustment": 0.5, "reason": "Short."})


def test_priors_disabled_flag_blocks_prior_tool(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_HERMES_PRIORS_ENABLED", "false")
    manager = HermesManager(tmp_path, model=FakePriorsModel(), backend=FakePriorsBackend(tmp_path))

    with pytest.raises(ValueError, match="disabled"):
        manager._call_tool("set_player_prior", {"player_id": 8, "adjustment": 0.5, "reason": "This should be blocked."}, None)


def test_prior_changes_optimized_squad(tmp_path) -> None:
    make_catalog(tmp_path)
    backend = FakePriorsBackend(tmp_path)
    strategy = type("S", (), {"planning_horizon": 3, "preferred_players": [], "differential_appetite": 0.0})()

    baseline, _ = backend.initial_squad(strategy)
    backend.set_prior(PlayerPrior(player_id=8, player_name="Player 8", adjustment=-1.5, reason="Rotation risk from manager comments."))
    adjusted, _ = backend.initial_squad(strategy)

    baseline_ids = {player.player_id for player in baseline.players}
    adjusted_ids = {player.player_id for player in adjusted.players}
    assert 8 in baseline_ids
    assert 8 not in adjusted_ids or adjusted.projected_points < baseline.projected_points


def test_initial_squad_uses_empty_squad_horizon_plan(tmp_path, monkeypatch) -> None:
    backend = FakePriorsBackend(tmp_path)
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

    squad, gameweek = backend.initial_squad(strategy)

    opening = captured["plan"].gameweeks[0]
    assert captured["state"].player_ids == []
    assert captured["state"].bank == 0
    assert captured["pre_season"] is True
    assert gameweek == opening.gameweek
    assert squad.players == opening.resulting_squad
    assert squad.bank == opening.bank_after


def test_full_run_records_priors_in_decision(tmp_path) -> None:
    make_catalog(tmp_path)
    manager = HermesManager(tmp_path, model=FakePriorsModel(), backend=FakePriorsBackend(tmp_path))

    result = manager.run()

    assert result.decision.action == "adopt_initial"
    assert len(result.decision.player_priors) == 1
    assert result.decision.player_priors[0].player_id == 8
    assert result.decision.player_priors[0].adjustment == -1.5
    assert manager.latest_state().player_priors[0].reason == "Rotation risk flagged in recent press conference."
