import pytest

from aifpl.current_projections import CurrentPlayerProjection
from aifpl.transfers import CurrentSquadState, plan_transfers


def candidate(identifier: int, position: str, club: str, cost: int, points: float) -> CurrentPlayerProjection:
    return CurrentPlayerProjection(
        player_id=identifier, player_name=f"Player {identifier}", position=position, club=club, cost=cost,
        projected_points=points, availability_multiplier=1.0,
    )


def current_pool() -> list[CurrentPlayerProjection]:
    return [
        candidate(1, "GK", "A", 40, 4), candidate(2, "GK", "B", 40, 3),
        candidate(3, "DEF", "A", 40, 5), candidate(4, "DEF", "B", 40, 4), candidate(5, "DEF", "C", 40, 3), candidate(6, "DEF", "D", 40, 2), candidate(7, "DEF", "E", 40, 1),
        candidate(8, "MID", "A", 50, 8), candidate(9, "MID", "B", 50, 7), candidate(10, "MID", "C", 50, 6), candidate(11, "MID", "D", 50, 5), candidate(12, "MID", "E", 50, 1),
        candidate(13, "FWD", "F", 50, 9), candidate(14, "FWD", "G", 50, 8), candidate(15, "FWD", "H", 50, 7),
    ]


def state(bank: int = 0, free_transfers: int = 1, max_transfers: int = 2) -> CurrentSquadState:
    return CurrentSquadState(player_ids=list(range(1, 16)), bank=bank, free_transfers=free_transfers, max_transfers=max_transfers)


def test_transfer_planner_holds_when_no_improvement_exists() -> None:
    plan = plan_transfers(current_pool(), state())

    assert plan.transfers_made == 0
    assert plan.outgoing == []
    assert plan.incoming == []


def test_transfer_planner_can_make_a_non_price_adjacent_upgrade_when_affordable() -> None:
    pool = current_pool() + [candidate(16, "MID", "F", 100, 20)]

    plan = plan_transfers(pool, state(bank=50))

    assert [player.player_id for player in plan.outgoing] == [11]
    assert [player.player_id for player in plan.incoming] == [16]
    assert plan.hit_cost == 0
    assert plan.bank_after == 0
    assert len(plan.starting_xi) == 11
    assert plan.captain in plan.starting_xi
    assert plan.objective_projected_points == sum(player.projected_points for player in plan.starting_xi) + plan.captain.projected_points
    assert plan.projected_points == plan.objective_projected_points
    assert plan.net_projected_points == plan.net_objective_points


def test_transfer_planner_charges_a_hit_when_second_move_still_improves_net_points() -> None:
    pool = current_pool() + [candidate(16, "MID", "F", 50, 20), candidate(17, "DEF", "F", 40, 10)]

    plan = plan_transfers(pool, state(free_transfers=0, max_transfers=2))

    assert plan.transfers_made == 2
    assert plan.hit_cost == 8
    assert {player.player_id for player in plan.incoming} == {16, 17}


def test_transfer_planner_rejects_unknown_current_player() -> None:
    with pytest.raises(ValueError, match="absent"):
        plan_transfers(current_pool(), CurrentSquadState(player_ids=list(range(1, 15)) + [99], bank=0, free_transfers=1))
