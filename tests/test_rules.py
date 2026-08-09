import pytest

from aifpl.rules import SquadRequest, SquadPlayer, select_best_lineup, transfer_hit_cost, TransferCostRequest, validate_squad


def player(identifier: int, position: str, club: str, points: float = 3.0, cost: int = 50) -> SquadPlayer:
    return SquadPlayer(id=identifier, name=f"Player {identifier}", position=position, club=club, cost=cost, projected_points=points)


def valid_squad() -> SquadRequest:
    players = [
        player(1, "GK", "A", 4), player(2, "GK", "B", 2),
        player(3, "DEF", "A", 6), player(4, "DEF", "B", 5), player(5, "DEF", "C", 4), player(6, "DEF", "D", 3), player(7, "DEF", "E", 2),
        player(8, "MID", "A", 9), player(9, "MID", "B", 8), player(10, "MID", "C", 7), player(11, "MID", "D", 6), player(12, "MID", "E", 1),
        player(13, "FWD", "F", 10), player(14, "FWD", "G", 5), player(15, "FWD", "C", 4),
    ]
    return SquadRequest(players=players, budget=1000)


def test_valid_squad_meets_composition_club_and_budget_rules() -> None:
    validation = validate_squad(valid_squad())

    assert validation.legal is True
    assert validation.position_counts == {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}


def test_validator_rejects_duplicate_player_and_club_limit() -> None:
    squad = valid_squad()
    players = list(squad.players)
    players[-1] = player(1, "FWD", "A")
    validation = validate_squad(SquadRequest(players=players, budget=1000))

    assert validation.legal is False
    assert any("unique" in error for error in validation.errors)
    assert any("maximum" in error for error in validation.errors)


def test_club_cap_normalizes_case_and_whitespace() -> None:
    squad = valid_squad()
    for index, club in zip((0, 1, 2, 3), ("Arsenal", " arsenal", "ARSENAL ", "Arsenal")):
        squad.players[index].club = club

    validation = validate_squad(squad)

    assert validation.legal is False
    assert any("maximum" in error for error in validation.errors)


def test_lineup_chooses_best_legal_formation_captain_and_bench() -> None:
    lineup = select_best_lineup(valid_squad())

    assert len(lineup.starters) == 11
    assert lineup.formation == "3-4-3"
    assert lineup.captain.id == 13
    assert lineup.vice_captain.id == 8
    assert lineup.bench[-1].position == "GK"
    assert lineup.projected_points == sum(player.projected_points for player in lineup.starters) + lineup.captain.projected_points


def test_lineup_refuses_illegal_squad() -> None:
    with pytest.raises(ValueError, match="illegal squad"):
        select_best_lineup(SquadRequest(players=valid_squad().players[:-1], budget=1000))


def test_transfer_hit_cost_respects_free_and_unlimited_transfers() -> None:
    assert transfer_hit_cost(TransferCostRequest(transfers_made=3, free_transfers=1)) == 8
    assert transfer_hit_cost(TransferCostRequest(transfers_made=10, free_transfers=1, unlimited_transfers=True)) == 0
