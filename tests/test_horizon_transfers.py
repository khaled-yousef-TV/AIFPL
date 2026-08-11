import pytest

from aifpl.horizon_transfers import HorizonSquadState, plan_horizon_transfers
from aifpl.odds_projections import OddsAdjustedGameweekProjection


def row(identifier: int, position: str, club: str, gameweek: int, points: float) -> OddsAdjustedGameweekProjection:
    return OddsAdjustedGameweekProjection(
        identifier, f"Player {identifier}", position, club, 50, gameweek, 1, 1, points,
    )


def pool() -> list[OddsAdjustedGameweekProjection]:
    players = [
        (1, "GK", "A", 4), (2, "GK", "B", 3),
        (3, "DEF", "A", 5), (4, "DEF", "B", 4), (5, "DEF", "C", 3),
        (6, "DEF", "D", 2), (7, "DEF", "E", 1),
        (8, "MID", "A", 8), (9, "MID", "B", 7), (10, "MID", "C", 6),
        (11, "MID", "D", 5), (12, "MID", "E", 1),
        (13, "FWD", "F", 9), (14, "FWD", "G", 8), (15, "FWD", "H", 7),
    ]
    return [row(identifier, position, club, gameweek, points) for gameweek in (1, 2, 3) for identifier, position, club, points in players]


def test_horizon_planner_rolls_free_transfers_without_needless_moves() -> None:
    plan = plan_horizon_transfers(pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1))

    assert [week.transfers_made for week in plan.gameweeks] == [0, 0, 0]
    assert [week.free_transfers_before for week in plan.gameweeks] == [1, 2, 3]
    assert plan.total_hit_cost == 0
    assert all(len(week.starting_xi) == 11 for week in plan.gameweeks)


def test_horizon_planner_rejects_noncontiguous_gameweeks() -> None:
    rows = [row for row in pool() if row.gameweek != 2]
    with pytest.raises(ValueError, match="contiguous"):
        plan_horizon_transfers(rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1))


def test_horizon_planner_executes_and_rolls_over_a_free_upgrade() -> None:
    rows = pool() + [row(16, "MID", "F", gameweek, 20) for gameweek in (1, 2, 3)]

    plan = plan_horizon_transfers(rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1))

    assert plan.gameweeks[0].transfers_made == 1
    assert [player.player_id for player in plan.gameweeks[0].incoming] == [16]
    assert plan.gameweeks[0].hit_cost == 0
    assert [week.free_transfers_before for week in plan.gameweeks] == [1, 1, 2]


def test_horizon_planner_builds_an_initial_squad_from_scratch() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=[], bank=0, free_transfers=0),
    )

    first = plan.gameweeks[0]
    assert len(first.resulting_squad) == 15
    assert len(first.starting_xi) == 11
    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert {player.player_id for player in first.resulting_squad} >= {1, 2, 13, 14, 15}
    assert all(week.gameweek == gameweek for week, gameweek in zip(plan.gameweeks, (1, 2, 3)))


def test_pre_season_planning_uses_free_transfers() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        pre_season=True,
    )

    assert plan.total_hit_cost == 0
    assert plan.total_net_projected_points == plan.total_projected_points
    assert all(week.hit_cost == 0 for week in plan.gameweeks)


def test_pre_season_allows_penalty_below_four() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        decision_hit_penalty=0.0, pre_season=True,
    )

    assert plan.total_hit_cost == 0
    assert plan.solver_status != "HOLD_FALLBACK"
