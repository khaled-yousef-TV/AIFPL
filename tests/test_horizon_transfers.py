from dataclasses import replace

import pytest

from aifpl.horizon_transfers import HorizonSquadState, plan_horizon_transfers
from aifpl.odds_projections import OddsAdjustedGameweekProjection


@pytest.fixture(autouse=True)
def disable_robustness_constraints(monkeypatch):
    monkeypatch.setenv("AIFPL_MIN_BANK_TENTHS", "0")
    monkeypatch.setenv("AIFPL_BENCH_MIN_PROJECTION", "0")
    monkeypatch.setenv("AIFPL_BENCH_WEIGHT", "0")
    monkeypatch.setenv("AIFPL_BANK_SHORTFALL_PENALTY", "0")
    monkeypatch.setenv("AIFPL_DEAD_BENCH_PENALTY", "0")


def row(
    identifier: int, position: str, club: str, gameweek: int, points: float, cost: int = 50,
) -> OddsAdjustedGameweekProjection:
    return OddsAdjustedGameweekProjection(
        identifier, f"Player {identifier}", position, club, cost, gameweek, 1, 1, points,
    )


def pool(gameweeks: tuple[int, ...] = (1, 2, 3)) -> list[OddsAdjustedGameweekProjection]:
    players = [
        (1, "GK", "A", 4), (2, "GK", "B", 3),
        (3, "DEF", "A", 5), (4, "DEF", "B", 4), (5, "DEF", "C", 3),
        (6, "DEF", "D", 2), (7, "DEF", "E", 1),
        (8, "MID", "A", 8), (9, "MID", "B", 7), (10, "MID", "C", 6),
        (11, "MID", "D", 5), (12, "MID", "E", 1),
        (13, "FWD", "F", 9), (14, "FWD", "G", 8), (15, "FWD", "H", 7),
    ]
    return [row(identifier, position, club, gameweek, points) for gameweek in gameweeks for identifier, position, club, points in players]


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
        pre_season=True,
    )

    first = plan.gameweeks[0]
    assert len(first.resulting_squad) == 15
    assert len(first.starting_xi) == 11
    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert {player.player_id for player in first.resulting_squad} >= {1, 2, 13, 14, 15}
    assert all(week.gameweek == gameweek for week, gameweek in zip(plan.gameweeks, (1, 2, 3)))
    assert [week.free_transfers_before for week in plan.gameweeks] == [5, 1, 2]
    assert first.hit_cost == 0


def test_pre_season_planning_resets_to_one_free_transfer_after_gw1() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        pre_season=True,
    )

    assert plan.total_hit_cost == 0
    assert plan.total_net_projected_points == plan.total_projected_points
    assert [week.free_transfers_before for week in plan.gameweeks] == [5, 1, 2]
    assert all(week.hit_cost == 0 for week in plan.gameweeks)


def test_pre_season_charges_hits_after_the_opening_gameweek(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_BENCH_WEIGHT", "1")
    rows = [
        replace(projection, projected_points=10.0)
        for projection in pool((1, 2, 3, 4))
    ]
    rows.extend([
        row(16, "FWD", "F", 1, 0), row(16, "FWD", "F", 2, 0), row(16, "FWD", "F", 3, 30), row(16, "FWD", "F", 4, 30),
        row(17, "DEF", "F", 1, 0), row(17, "DEF", "F", 2, 0), row(17, "DEF", "F", 3, 0), row(17, "DEF", "F", 4, 30),
        row(18, "MID", "G", 1, 0), row(18, "MID", "G", 2, 0), row(18, "MID", "G", 3, 0), row(18, "MID", "G", 4, 30),
        row(19, "FWD", "I", 1, 0), row(19, "FWD", "I", 2, 0), row(19, "FWD", "I", 3, 0), row(19, "FWD", "I", 4, 30),
    ])

    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        pre_season=True,
    )

    assert [week.transfers_made for week in plan.gameweeks] == [0, 0, 1, 3]
    assert [week.free_transfers_before for week in plan.gameweeks] == [5, 1, 2, 2]
    assert [week.hit_cost for week in plan.gameweeks] == [0, 0, 0, 4]
    assert plan.total_hit_cost == 4


def test_pre_season_allows_penalty_below_four_for_the_opening_week_only() -> None:
    plan = plan_horizon_transfers(
        [projection for projection in pool() if projection.gameweek == 1],
        HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        decision_hit_penalty=0.0, pre_season=True,
    )

    assert plan.total_hit_cost == 0
    assert plan.solver_status != "HOLD_FALLBACK"


def test_pre_season_rejects_discounted_post_deadline_hits() -> None:
    with pytest.raises(ValueError, match="cannot be lower"):
        plan_horizon_transfers(
            pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
            decision_hit_penalty=0.0, pre_season=True,
        )


def robust_pool() -> list[OddsAdjustedGameweekProjection]:
    cheap = [
        (16, "GK", "I", 30, 2.0), (17, "DEF", "J", 30, 2.0), (18, "DEF", "K", 30, 2.0),
        (19, "MID", "L", 30, 2.0), (20, "MID", "M", 30, 2.0), (21, "FWD", "N", 30, 2.0),
    ]
    return pool() + [
        row(identifier, position, club, gameweek, points, cost)
        for gameweek in (1, 2, 3)
        for identifier, position, club, cost, points in cheap
    ]


def test_bank_and_bench_soft_constraints_never_fail(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_MIN_BANK_TENTHS", "50")
    monkeypatch.setenv("AIFPL_BENCH_MIN_PROJECTION", "2.0")
    monkeypatch.setenv("AIFPL_BENCH_WEIGHT", "0")
    monkeypatch.setenv("AIFPL_BANK_SHORTFALL_PENALTY", "0.5")
    monkeypatch.setenv("AIFPL_DEAD_BENCH_PENALTY", "2.0")

    plan = plan_horizon_transfers(
        robust_pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=0, free_transfers=1),
        pre_season=False,
    )

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE", "HOLD_FALLBACK")


def test_bank_shortfall_penalty_prefers_bank_reserve(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_MIN_BANK_TENTHS", "50")
    monkeypatch.setenv("AIFPL_BENCH_MIN_PROJECTION", "0")
    monkeypatch.setenv("AIFPL_BENCH_WEIGHT", "0")
    monkeypatch.setenv("AIFPL_BANK_SHORTFALL_PENALTY", "0.5")
    monkeypatch.setenv("AIFPL_DEAD_BENCH_PENALTY", "0")

    plan = plan_horizon_transfers(
        robust_pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=0, free_transfers=5),
        pre_season=True,
    )

    assert plan.gameweeks[0].bank_after >= 50


def test_dead_bench_penalty_prefers_playing_bench(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_MIN_BANK_TENTHS", "0")
    monkeypatch.setenv("AIFPL_BENCH_MIN_PROJECTION", "2.0")
    monkeypatch.setenv("AIFPL_BENCH_WEIGHT", "0")
    monkeypatch.setenv("AIFPL_BANK_SHORTFALL_PENALTY", "0")
    monkeypatch.setenv("AIFPL_DEAD_BENCH_PENALTY", "2.0")

    plan = plan_horizon_transfers(
        robust_pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=5),
        pre_season=True,
    )

    first = plan.gameweeks[0]
    xi_ids = {player.player_id for player in first.starting_xi}
    bench_players = [player for player in first.resulting_squad if player.player_id not in xi_ids]
    dead = sum(1 for player in bench_players if player.projected_points < 2.0)
    assert dead <= 2


def test_pre_season_hold_fallback_suppressed_when_squad_violates_robustness(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_MIN_BANK_TENTHS", "50")
    monkeypatch.setenv("AIFPL_BENCH_MIN_PROJECTION", "2.0")
    monkeypatch.setenv("AIFPL_BENCH_WEIGHT", "0")
    monkeypatch.setenv("AIFPL_BANK_SHORTFALL_PENALTY", "0.5")
    monkeypatch.setenv("AIFPL_DEAD_BENCH_PENALTY", "2.0")

    plan = plan_horizon_transfers(
        robust_pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=0, free_transfers=1),
        pre_season=True,
    )

    assert plan.solver_status != "HOLD_FALLBACK"
    assert plan.gameweeks[0].bank_after >= 50


def test_mid_season_hold_fallback_uses_full_objective_accounting(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_MIN_BANK_TENTHS", "50")
    monkeypatch.setenv("AIFPL_BENCH_MIN_PROJECTION", "2.0")
    monkeypatch.setenv("AIFPL_BENCH_WEIGHT", "0")
    monkeypatch.setenv("AIFPL_BANK_SHORTFALL_PENALTY", "0.5")
    monkeypatch.setenv("AIFPL_DEAD_BENCH_PENALTY", "2.0")

    plan = plan_horizon_transfers(
        robust_pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=0, free_transfers=1),
        pre_season=False,
    )

    assert plan.solver_status != "HOLD_FALLBACK"
    assert any(week.transfers_made > 0 for week in plan.gameweeks)


def test_transfer_penalty_discourages_marginal_churn(monkeypatch) -> None:
    rows = pool() + [row(16, "MID", "I", gameweek, 6.0) for gameweek in (1, 2, 3)]
    state = HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1)

    monkeypatch.setenv("AIFPL_TRANSFER_PENALTY", "0")
    churny = plan_horizon_transfers(rows, state)

    monkeypatch.setenv("AIFPL_TRANSFER_PENALTY", "10")
    frugal = plan_horizon_transfers(rows, state)

    assert churny.gameweeks[0].transfers_made == 1
    assert frugal.gameweeks[0].transfers_made == 0


def test_robustness_score_is_bounded_and_averaged() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
    )

    assert all(0 <= week.robustness_score <= 100 for week in plan.gameweeks)
    assert plan.robustness_score == round(
        sum(week.robustness_score for week in plan.gameweeks) / len(plan.gameweeks), 1,
    )


def test_horizon_allows_sub_floor_starter_when_forced(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_BENCH_MIN_PROJECTION", "2.0")
    rows = pool()
    for gameweek in (1, 2, 3):
        for identifier in (13, 14, 15):
            index = next(
                i for i, row in enumerate(rows)
                if row.player_id == identifier and row.gameweek == gameweek
            )
            rows[index] = replace(rows[index], projected_points=0.5)

    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
    )

    forwards = [player for player in plan.gameweeks[0].starting_xi if player.position == "FWD"]
    assert len(forwards) == 1
    assert forwards[0].projected_points < 2.0
