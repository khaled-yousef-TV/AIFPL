from dataclasses import replace
from itertools import combinations

import pytest

import aifpl.horizon_transfers as horizon_module
from aifpl.horizon_transfers import HorizonSquadState, plan_horizon_transfers
from aifpl.objective_accounting import HorizonPlanValidationError, validate_horizon_plan
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
    ownership: float = 0.0,
) -> OddsAdjustedGameweekProjection:
    return OddsAdjustedGameweekProjection(
        identifier, f"Player {identifier}", position, club, cost, gameweek, 1, 1, points,
        selected_by_percent=ownership,
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


def max_projected_lineup(squad: list, points_by_id: dict[int, float]) -> tuple[float, set[int]]:
    groups: dict[str, list[int]] = {}
    for player in squad:
        groups.setdefault(player.position, []).append(player.player_id)
    best_sum, best_ids = 0.0, set()
    for gk in combinations(groups["GK"], 1):
        for def_count in (3, 4, 5):
            for df in combinations(groups["DEF"], def_count):
                for mid_count in (2, 3, 4, 5):
                    for md in combinations(groups["MID"], mid_count):
                        fwd_count = 11 - 1 - def_count - mid_count
                        if 1 <= fwd_count <= 3:
                            for fw in combinations(groups["FWD"], fwd_count):
                                total = sum(points_by_id[player_id] for player_id in gk + df + md + fw)
                                if total > best_sum:
                                    best_sum, best_ids = total, set(gk + df + md + fw)
    return best_sum, best_ids


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


def test_normal_week_allows_free_transfers_plus_the_paid_safety_cap() -> None:
    rows = pool((2,))
    upgrades = [
        (16, "GK", "I"),
        (17, "DEF", "J"), (18, "DEF", "K"), (19, "DEF", "L"), (20, "DEF", "M"), (21, "DEF", "N"),
        (22, "MID", "O"), (23, "MID", "P"), (24, "MID", "Q"), (25, "MID", "R"), (26, "MID", "S"),
        (27, "FWD", "T"), (28, "FWD", "U"), (29, "FWD", "V"),
    ]
    rows.extend(row(identifier, position, club, 2, 40.0) for identifier, position, club in upgrades)

    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        churn_penalty=0.0,
    )

    assert plan.gameweeks[0].transfers_made == 3
    assert plan.gameweeks[0].hit_cost == 8


def test_normal_week_can_use_all_banked_free_transfers(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_PAID_TRANSFER_SAFETY_CAP", "0")
    rows = pool((2,))
    upgrades = [
        (16, "GK", "I"),
        (17, "DEF", "J"), (18, "DEF", "K"), (19, "DEF", "L"), (20, "DEF", "M"), (21, "DEF", "N"),
        (22, "MID", "O"), (23, "MID", "P"), (24, "MID", "Q"), (25, "MID", "R"), (26, "MID", "S"),
        (27, "FWD", "T"), (28, "FWD", "U"), (29, "FWD", "V"),
    ]
    rows.extend(row(identifier, position, club, 2, 40.0) for identifier, position, club in upgrades)

    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=5),
        churn_penalty=0.0,
    )

    assert plan.gameweeks[0].transfers_made == 5
    assert plan.gameweeks[0].hit_cost == 0
    assert plan.gameweeks[0].free_transfers_after == 1


def test_paid_transfer_safety_cap_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_PAID_TRANSFER_SAFETY_CAP", "1")
    rows = pool((2,))
    rows.extend([
        row(16, "MID", "I", 2, 20.0),
        row(17, "DEF", "J", 2, 20.0),
        row(18, "FWD", "K", 2, 20.0),
    ])

    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        churn_penalty=0.0,
    )

    assert plan.gameweeks[0].transfers_made <= 2


def test_normal_week_skips_a_marginal_second_transfer_hit() -> None:
    rows = pool((2,)) + [
        row(16, "MID", "I", 2, 20.0),
        row(17, "MID", "J", 2, 5.1),
    ]

    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        churn_penalty=0.0,
    )

    assert plan.gameweeks[0].transfers_made == 1
    assert plan.gameweeks[0].hit_cost == 0


def test_wildcard_makes_transfers_unlimited_and_preserves_free_transfer() -> None:
    rows = pool() + [
        row(16, "MID", "F", gameweek, 40.0) for gameweek in (1, 2, 3)
    ] + [
        row(17, "DEF", "I", gameweek, 40.0) for gameweek in (1, 2, 3)
    ] + [
        row(18, "FWD", "J", gameweek, 40.0) for gameweek in (1, 2, 3)
    ]
    state = HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1)

    plan = plan_horizon_transfers(rows, state, churn_penalty=0.0, active_chip="wildcard")

    first = plan.gameweeks[0]
    assert plan.active_chip == "wildcard"
    assert first.active_chip == "wildcard"
    assert first.transfers_made > state.free_transfers
    assert first.unlimited_transfers is True
    assert first.hit_cost == 0
    assert first.free_transfers_after == 2
    assert plan.gameweeks[1].free_transfers_before == 2
    assert plan.total_hit_cost == 0


def test_free_hit_restores_squad_bank_and_transfer_balance_for_next_week() -> None:
    rows = pool() + [
        row(16, "MID", "F", 1, 40.0), row(16, "MID", "F", 2, 0.0), row(16, "MID", "F", 3, 0.0),
        row(17, "DEF", "I", 1, 40.0), row(17, "DEF", "I", 2, 0.0), row(17, "DEF", "I", 3, 0.0),
        row(18, "FWD", "J", 1, 40.0), row(18, "FWD", "J", 2, 0.0), row(18, "FWD", "J", 3, 0.0),
    ]
    state = HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1)

    plan = plan_horizon_transfers(rows, state, churn_penalty=10.0, active_chip="free_hit")

    first, second = plan.gameweeks[:2]
    assert plan.active_chip == "free_hit"
    assert first.active_chip == "free_hit"
    assert first.transfers_made > state.free_transfers
    assert first.unlimited_transfers is True
    assert first.hit_cost == 0
    assert first.free_transfers_after == 2
    assert second.free_transfers_before == 2
    assert second.incoming == []
    assert second.outgoing == []
    assert {player.player_id for player in second.resulting_squad} == set(state.player_ids)
    assert second.bank_before == state.bank
    assert second.bank_after == state.bank


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


def test_horizon_planner_captains_the_highest_projected_starter() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
    )

    for week in plan.gameweeks:
        top = max(week.starting_xi, key=lambda player: player.projected_points)
        assert week.captain.player_id == top.player_id


def test_horizon_planner_sets_the_second_highest_starter_as_vice_captain() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
    )

    for week in plan.gameweeks:
        ranked = sorted(week.starting_xi, key=lambda player: player.projected_points, reverse=True)
        assert week.vice_captain is not None
        assert week.vice_captain.player_id == ranked[1].player_id


def test_normal_horizon_lineup_is_the_best_legal_xi_from_its_selected_squad() -> None:
    rows = pool()
    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
    )

    for week in plan.gameweeks:
        points_by_id = {
            projection.player_id: projection.projected_points
            for projection in rows
            if projection.gameweek == week.gameweek
        }
        _, expected_ids = max_projected_lineup(week.resulting_squad, points_by_id)
        assert {player.player_id for player in week.starting_xi} == expected_ids


def test_pre_season_planning_resets_to_one_free_transfer_after_gw1() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        pre_season=True,
    )

    assert plan.total_hit_cost == 0
    assert plan.total_net_projected_points == plan.total_projected_points
    assert [week.free_transfers_before for week in plan.gameweeks] == [5, 1, 2]
    assert [week.free_transfers_after for week in plan.gameweeks] == [1, 2, 3]
    assert [week.unlimited_transfers for week in plan.gameweeks] == [True, False, False]
    assert all(week.hit_cost == 0 for week in plan.gameweeks)


def test_normal_planning_reports_free_transfers_after_each_week() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
    )

    assert [week.free_transfers_before for week in plan.gameweeks] == [1, 2, 3]
    assert [week.free_transfers_after for week in plan.gameweeks] == [2, 3, 4]
    assert all(not week.unlimited_transfers for week in plan.gameweeks)


def test_horizon_objective_is_reported_as_a_complete_decomposition() -> None:
    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
    )

    for week in plan.gameweeks:
        components = week.objective_components
        component_total = sum(
            components[key]
            for key in (
                "starter_points", "captain_points", "bench_points",
                "strategy_hit_penalty", "churn_penalty", "bank_shortfall_penalty",
                "dead_bench_penalty", "preferred_bonus", "differential_bonus",
            )
        )
        assert week.objective_net_points == round(component_total, 4)
        assert components["total"] == week.objective_net_points
        assert 0 <= components["information_weight"] <= 1
        assert 0 < components["forecast_distance_weight"] <= 1
    assert plan.objective_value == round(sum(week.objective_net_points for week in plan.gameweeks), 4)
    assert plan.objective_components["total"] == plan.objective_value


def test_feasible_solver_accepts_valid_reported_postprocessing(monkeypatch) -> None:
    real_solver = horizon_module.cp_model.CpSolver

    class FeasibleIncumbentSolver:
        def __init__(self) -> None:
            self._solver = real_solver()

        def __getattr__(self, name):
            return getattr(self._solver, name)

        def solve(self, model):
            status = self._solver.solve(model)
            assert status in (horizon_module.cp_model.OPTIMAL, horizon_module.cp_model.FEASIBLE)
            return horizon_module.cp_model.FEASIBLE

        @property
        def objective_value(self):
            return self._solver.objective_value + 10_000

    monkeypatch.setattr(horizon_module.cp_model, "CpSolver", FeasibleIncumbentSolver)

    plan = plan_horizon_transfers(
        pool(), HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
    )

    assert plan.solver_status == "FEASIBLE"


def test_post_solve_validator_rejects_tampered_bank_accounting() -> None:
    state = HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1)
    plan = plan_horizon_transfers(pool(), state)
    tampered = replace(
        plan,
        gameweeks=[replace(plan.gameweeks[0], bank_after=plan.gameweeks[0].bank_after + 1)]
        + plan.gameweeks[1:],
    )

    with pytest.raises(HorizonPlanValidationError, match="bank"):
        validate_horizon_plan(tampered, pool(), state)


def test_zero_odds_week_has_a_bounded_information_weight(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_HORIZON_MIN_CONFIDENCE_WEIGHT", "0.25")
    rows = pool()
    rows = [
        replace(projection, odds_backed_fixture_count=0)
        if projection.gameweek == 2 else projection
        for projection in rows
    ]

    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
    )

    zero_odds = plan.gameweeks[1].objective_components
    assert zero_odds["odds_coverage"] == 0.0
    assert zero_odds["information_weight"] == 0.25
    assert zero_odds["week_weight"] < plan.gameweeks[0].objective_components["week_weight"]


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
        row(20, "DEF", "J", 1, 0), row(20, "DEF", "J", 2, 0), row(20, "DEF", "J", 3, 30), row(20, "DEF", "J", 4, 30),
    ])

    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1),
        pre_season=True,
    )

    assert [week.transfers_made for week in plan.gameweeks] == [0, 0, 2, 3]
    assert [week.free_transfers_before for week in plan.gameweeks] == [5, 1, 2, 1]
    assert [week.hit_cost for week in plan.gameweeks] == [0, 0, 0, 8]
    assert plan.total_hit_cost == 8


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


def test_churn_penalty_override_supersedes_the_environment(monkeypatch) -> None:
    rows = pool() + [row(16, "MID", "I", gameweek, 6.0) for gameweek in (1, 2, 3)]
    state = HorizonSquadState(player_ids=list(range(1, 16)), bank=250, free_transfers=1)
    monkeypatch.setenv("AIFPL_TRANSFER_PENALTY", "10")

    free_churn = plan_horizon_transfers(rows, state, churn_penalty=0.0)
    pricey_churn = plan_horizon_transfers(rows, state, churn_penalty=10.0)

    assert free_churn.gameweeks[0].transfers_made == 1
    assert pricey_churn.gameweeks[0].transfers_made == 0


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


def test_horizon_lineup_is_max_projected_xi_regardless_of_differential_appetite(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_BANK_SHORTFALL_PENALTY", "1")
    monkeypatch.setenv("AIFPL_MIN_BANK_TENTHS", "50")
    rows = []
    for gameweek in (1, 2):
        rows.append(row(1, "GK", "A", gameweek, 4, cost=40))
        rows.append(row(2, "GK", "C", gameweek, 3, cost=40))
        for identifier, points in ((3, 5.0), (4, 4.0), (5, 3.0), (6, 2.0), (7, 1.0)):
            rows.append(row(identifier, "DEF", "CDEFG"[identifier - 3], gameweek, points, cost=60))
        for identifier, points in ((8, 8.0), (9, 7.0), (10, 6.0), (11, 5.0), (12, 1.0)):
            rows.append(row(identifier, "MID", "ABCDG"[identifier - 8], gameweek, points, cost=60))
        rows.append(row(13, "FWD", "F", gameweek, 9.0, cost=100))
        rows.append(row(14, "FWD", "G", gameweek, 8.0, cost=95, ownership=25.6))
        rows.append(row(15, "FWD", "H", gameweek, 7.0, cost=120))
        rows.append(row(16, "FWD", "I", gameweek, 0.0, cost=45, ownership=0.2))

    plan = plan_horizon_transfers(
        rows, HorizonSquadState(player_ids=[], bank=0, free_transfers=0),
        differential_appetite=1.0, pre_season=True,
    )

    week = plan.gameweeks[0]
    squad_ids = {player.player_id for player in week.resulting_squad}
    assert 16 in squad_ids
    points_by_id = {current.player_id: current.projected_points for current in rows if current.gameweek == 1}
    best_sum, best_ids = max_projected_lineup(week.resulting_squad, points_by_id)
    xi_ids = {player.player_id for player in week.starting_xi}
    assert 16 not in xi_ids
    assert xi_ids == best_ids
    assert week.projected_points == round(best_sum + max(points_by_id[player_id] for player_id in best_ids), 4)
