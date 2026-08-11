import pytest

from aifpl.current_projections import CurrentPlayerProjection
from aifpl.optimizer import SquadOptimizationError, optimize_squad


def candidate(identifier: int, position: str, club: str, cost: int, points: float) -> CurrentPlayerProjection:
    return CurrentPlayerProjection(
        player_id=identifier, player_name=f"Player {identifier}", position=position, club=club, cost=cost,
        projected_points=points, availability_multiplier=1.0,
    )


def full_pool() -> list[CurrentPlayerProjection]:
    candidates = [
        candidate(1, "GK", "A", 40, 4), candidate(2, "GK", "B", 40, 3),
        candidate(3, "DEF", "A", 40, 5), candidate(4, "DEF", "B", 40, 4), candidate(5, "DEF", "C", 40, 3), candidate(6, "DEF", "D", 40, 2), candidate(7, "DEF", "E", 40, 1),
        candidate(8, "MID", "A", 50, 8), candidate(9, "MID", "B", 50, 7), candidate(10, "MID", "C", 50, 6), candidate(11, "MID", "D", 50, 5), candidate(12, "MID", "E", 50, 4),
        candidate(13, "FWD", "F", 50, 9), candidate(14, "FWD", "G", 50, 8), candidate(15, "FWD", "H", 50, 7),
    ]
    return candidates


def test_optimizer_selects_a_complete_legal_squad() -> None:
    result = optimize_squad(full_pool(), budget=680)

    assert len(result.players) == 15
    assert result.total_cost == 680
    assert result.bank == 0
    assert result.solver_status == "OPTIMAL"
    assert len(result.starting_xi) == 11
    assert result.captain in result.starting_xi
    assert result.projected_points == sum(player.projected_points for player in result.starting_xi) + result.captain.projected_points


def test_optimizer_uses_higher_projected_affordable_candidate_not_price_proximity() -> None:
    candidates = full_pool()
    candidates.append(candidate(16, "MID", "F", 100, 20))

    result = optimize_squad(candidates, budget=750)

    assert 16 in {player.player_id for player in result.players}
    assert 16 in {player.player_id for player in result.starting_xi}


def test_optimizer_rejects_pool_that_cannot_form_a_squad() -> None:
    with pytest.raises(SquadOptimizationError, match="No legal squad"):
        optimize_squad(full_pool()[:-1], budget=1000)


def test_optimizer_rejects_duplicate_candidate_ids() -> None:
    with pytest.raises(ValueError, match="unique"):
        optimize_squad(full_pool() + [full_pool()[0]])


def test_optimizer_enforces_club_cap_with_normalized_names() -> None:
    candidates = full_pool() + [candidate(16, "FWD", " a ", 50, 100)]

    result = optimize_squad(candidates, budget=1000)

    assert 16 not in {player.player_id for player in result.players}
