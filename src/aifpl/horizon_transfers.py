from __future__ import annotations

import os
from dataclasses import dataclass, field

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field

from aifpl.config import (
    bench_min_projection,
    paid_transfer_safety_cap,
    robustness_bank_scale_tenths,
)
from aifpl.current_projections import CurrentPlayerProjection
from aifpl.odds_projections import OddsAdjustedGameweekProjection
from aifpl.objective_accounting import (
    DEAD_BENCH_ALLOWANCE,
    OBJECTIVE_SCALE,
    aggregate_objective_components,
    best_objective_lineup,
    bank_shortfall_coefficient,
    bench_coefficient,
    captain_coefficient,
    churn_coefficient,
    dead_bench_coefficient,
    differential_coefficient,
    HorizonObjectiveSettings,
    HorizonWeekWeight,
    horizon_objective_breakdown,
    horizon_objective_settings,
    horizon_week_weights,
    preferred_coefficient,
    starter_coefficient,
    strategy_hit_coefficient,
    validate_horizon_plan,
)
from aifpl.optimizer import SquadOptimizationError, optimize_squad
from aifpl.projection_catalogs import _aggregate
from aifpl.rules import DEFAULT_BUDGET_TENTHS, SquadPlayer, SquadRequest, club_key, select_best_lineup, validate_squad


# Bump when plan-generation semantics change (accounting, objective, captain
# selection, robustness, ...). Committed plans record their version so stale
# opening squads can be regenerated deterministically.
PLANNER_VERSION = "v6"


class HorizonSquadState(BaseModel):
    player_ids: list[int] = Field(min_length=0, max_length=15)
    bank: int = Field(ge=0, le=5000, description="Available funds in FPL tenths of a million")
    free_transfers: int = Field(ge=0, le=5, description="Free transfers available before the first gameweek")
    purchase_prices: dict[int, int] | None = Field(
        default=None, description="Purchase prices in FPL tenths of a million, keyed by player ID"
    )


@dataclass(frozen=True)
class HorizonGameweekPlan:
    """One gameweek of a plan; all bank values use FPL tenths of a million."""

    gameweek: int
    outgoing: list[CurrentPlayerProjection]
    incoming: list[CurrentPlayerProjection]
    resulting_squad: list[CurrentPlayerProjection]
    starting_xi: list[CurrentPlayerProjection]
    captain: CurrentPlayerProjection
    transfers_made: int
    free_transfers_before: int
    hit_cost: int
    bank_after: int
    projected_points: float
    net_projected_points: float
    odds_coverage: float
    objective_net_points: float = 0.0
    robustness_score: float = 0.0
    unlimited_transfers: bool = False
    free_transfers_after: int | None = None
    vice_captain: CurrentPlayerProjection | None = None
    bank_before: int = 0
    purchase_value: int = 0
    sale_value: int = 0
    objective_components: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class HorizonTransferPlan:
    """A validated multi-gameweek plan with a persisted objective breakdown."""

    gameweeks: list[HorizonGameweekPlan]
    total_projected_points: float
    total_hit_cost: int
    total_net_projected_points: float
    solver_status: str
    methodology: str
    robustness_score: float = 0.0
    objective_value: float = 0.0
    objective_components: dict[str, float] = field(default_factory=dict)


def plan_horizon_transfers(
    rows: list[OddsAdjustedGameweekProjection], state: HorizonSquadState,
    decision_hit_penalty: float = 4.0,
    preferred_player_ids: set[int] | None = None,
    differential_appetite: float = 0.0,
    pre_season: bool = False,
    churn_penalty: float | None = None,
    hold_only: bool = False,
) -> HorizonTransferPlan:
    """Plan transfers and lineups across the horizon.

    differential_appetite in 0..1 tilts squad purchases toward under-owned players:
    each selected player earns up to appetite * projected_points * (1 - ownership/100)
    extra objective value, so it never overrides a positive projection. The starting
    XI and captain are selected by the same objective that is reported with the plan.
    """
    if not 0 <= differential_appetite <= 1:
        raise ValueError("differential_appetite must be within 0..1")
    if decision_hit_penalty < 0:
        raise ValueError("decision_hit_penalty must not be negative")
    if churn_penalty is not None and churn_penalty < 0:
        raise ValueError("churn_penalty must not be negative")
    gameweeks = sorted({row.gameweek for row in rows})
    if not 1 <= len(gameweeks) <= 6 or gameweeks != list(range(gameweeks[0], gameweeks[-1] + 1)):
        raise ValueError("Horizon projection catalog must contain 1 to 6 contiguous gameweeks")
    if pre_season and gameweeks[0] != 1:
        raise ValueError("Pre-season planning must begin in gameweek 1")
    if decision_hit_penalty < 4 and (not pre_season or len(gameweeks) > 1):
        raise ValueError("Decision hit penalty cannot be lower than the actual four-point hit")
    by_player_gameweek = {(row.player_id, row.gameweek): row for row in rows}
    if len(by_player_gameweek) != len(rows):
        raise ValueError("Projection catalog contains duplicate player-gameweek rows")
    player_ids = sorted({row.player_id for row in rows})
    if any((player_id, gameweek) not in by_player_gameweek for player_id in player_ids for gameweek in gameweeks):
        raise ValueError("Projection catalog has an incomplete player-gameweek horizon")
    if any(
        row.fixture_count < 0
        or row.odds_backed_fixture_count < 0
        or row.odds_backed_fixture_count > row.fixture_count
        for row in rows
    ):
        raise ValueError("Projection catalog contains invalid fixture or odds coverage counts")
    metadata = {player_id: by_player_gameweek[(player_id, gameweeks[0])] for player_id in player_ids}
    methodologies = {row.methodology for row in rows}
    if len(methodologies) != 1:
        raise ValueError("Projection catalog must use one methodology")
    for player_id in player_ids:
        expected = metadata[player_id]
        if any(
            (row.player_name, row.position, row.club, row.cost, row.methodology)
            != (expected.player_name, expected.position, expected.club, expected.cost, expected.methodology)
            for row in (by_player_gameweek[(player_id, gameweek)] for gameweek in gameweeks)
        ):
            raise ValueError(f"Inconsistent projection metadata for player {player_id}")
    current_ids = set(state.player_ids)
    if len(current_ids) not in (0, 15):
        raise ValueError("Current squad player IDs must contain either 15 players or none for an initial adoption")
    missing = current_ids - set(player_ids)
    if missing:
        raise ValueError(f"Current squad contains players absent from the projection catalog: {sorted(missing)}")
    initial = [_candidate(metadata[player_id], gameweeks[0]) for player_id in current_ids]
    purchase_prices = state.purchase_prices or {player.player_id: player.cost for player in initial}
    if set(purchase_prices) != current_ids:
        raise ValueError("purchase_prices must contain exactly the current squad IDs")
    if any(price < 0 for price in purchase_prices.values()):
        raise ValueError("purchase_prices must not be negative")
    sale_values = {
        player.player_id: (
            player.cost if player.cost <= purchase_prices[player.player_id]
            else purchase_prices[player.player_id] + (player.cost - purchase_prices[player.player_id]) // 2
        )
        for player in initial
    }
    if current_ids:
        validation = validate_squad(SquadRequest(
            players=[_squad_player(player) for player in initial],
            budget=sum(player.cost for player in initial) + state.bank,
        ))
        if not validation.legal:
            raise ValueError("Current squad is invalid: " + "; ".join(validation.errors))

    initial_free_transfers = 5 if pre_season else state.free_transfers
    if not 1 <= initial_free_transfers <= 5:
        raise ValueError("Free transfers before the horizon must be within 1..5")
    paid_transfer_cap = paid_transfer_safety_cap()
    objective_settings = horizon_objective_settings(decision_hit_penalty, churn_penalty)
    week_weights = horizon_week_weights(rows, gameweeks, objective_settings)
    min_bank = objective_settings.minimum_bank_tenths
    bench_floor = bench_min_projection()
    bank_scale = robustness_bank_scale_tenths()

    if hold_only:
        if not current_ids:
            raise ValueError("A hold plan requires an existing 15-player squad")
        plans = _hold_plans(
            rows, state, gameweeks, by_player_gameweek, current_ids, pre_season,
            bench_floor, bank_scale, objective_settings, week_weights,
            preferred_player_ids, differential_appetite,
        )
        result = HorizonTransferPlan(
            gameweeks=plans,
            total_projected_points=round(sum(plan.projected_points for plan in plans), 4),
            total_hit_cost=sum(plan.hit_cost for plan in plans),
            total_net_projected_points=round(sum(plan.net_projected_points for plan in plans), 4),
            solver_status="HOLD_ONLY",
            methodology=rows[0].methodology,
            robustness_score=round(sum(plan.robustness_score for plan in plans) / max(1, len(plans)), 1),
            objective_value=round(sum(plan.objective_net_points for plan in plans), 4),
            objective_components=aggregate_objective_components(
                [plan.objective_components for plan in plans]
            ),
        )
        try:
            validate_horizon_plan(
                result, rows, state, pre_season=pre_season,
                decision_hit_penalty=decision_hit_penalty,
                churn_penalty=churn_penalty,
                preferred_player_ids=preferred_player_ids,
                differential_appetite=differential_appetite,
                bench_floor=bench_floor,
                paid_transfer_cap=paid_transfer_cap,
                settings=objective_settings,
                initial_purchase_prices=purchase_prices,
            )
        except ValueError as exc:
            raise SquadOptimizationError(f"Post-solve hold validation failed: {exc}") from exc
        return result

    model = cp_model.CpModel()
    selected: dict[tuple[int, int], cp_model.IntVar] = {}
    starter: dict[tuple[int, int], cp_model.IntVar] = {}
    captain: dict[tuple[int, int], cp_model.IntVar] = {}
    bench: dict[tuple[int, int], cp_model.IntVar] = {}
    dead_bench: dict[tuple[int, int], cp_model.IntVar] = {}
    incoming: dict[tuple[int, int], cp_model.IntVar] = {}
    outgoing: dict[tuple[int, int], cp_model.IntVar] = {}
    original_holding: dict[tuple[int, int], cp_model.IntVar] = {}
    original_outgoing: dict[tuple[int, int], cp_model.IntVar] = {}
    transfer_counts: list[cp_model.IntVar] = []
    free_transfers: list[cp_model.IntVar] = []
    excess_transfers: list[cp_model.IntVar] = []
    banks: list[cp_model.IntVar] = []
    bank_shortfalls: list[cp_model.IntVar] = []
    dead_bench_excess: list[cp_model.IntVar] = []
    for week_index, gameweek in enumerate(gameweeks):
        for player_id in player_ids:
            selected[player_id, week_index] = model.new_bool_var(f"selected_{player_id}_{gameweek}")
            starter[player_id, week_index] = model.new_bool_var(f"starter_{player_id}_{gameweek}")
            captain[player_id, week_index] = model.new_bool_var(f"captain_{player_id}_{gameweek}")
            bench[player_id, week_index] = model.new_bool_var(f"bench_{player_id}_{gameweek}")
            dead_bench[player_id, week_index] = model.new_bool_var(f"dead_bench_{player_id}_{gameweek}")
            incoming[player_id, week_index] = model.new_bool_var(f"in_{player_id}_{gameweek}")
            outgoing[player_id, week_index] = model.new_bool_var(f"out_{player_id}_{gameweek}")
            original_holding[player_id, week_index] = model.new_bool_var(f"original_{player_id}_{gameweek}")
            original_outgoing[player_id, week_index] = model.new_bool_var(f"original_out_{player_id}_{gameweek}")
            model.add(starter[player_id, week_index] <= selected[player_id, week_index])
            model.add(captain[player_id, week_index] <= starter[player_id, week_index])
            model.add(selected[player_id, week_index] == starter[player_id, week_index] + bench[player_id, week_index])
            model.add(dead_bench[player_id, week_index] <= bench[player_id, week_index])
            model.add(by_player_gameweek[player_id, gameweek].projected_points >= bench_floor).only_enforce_if(
                selected[player_id, week_index], bench[player_id, week_index], dead_bench[player_id, week_index].Not()
            )

            previous = 1 if week_index == 0 and player_id in current_ids else 0 if week_index == 0 else selected[player_id, week_index - 1]
            model.add(incoming[player_id, week_index] >= selected[player_id, week_index] - previous)
            model.add(outgoing[player_id, week_index] >= previous - selected[player_id, week_index])
            model.add(incoming[player_id, week_index] <= selected[player_id, week_index])
            model.add(incoming[player_id, week_index] <= 1 - previous)
            model.add(outgoing[player_id, week_index] <= previous)
            model.add(outgoing[player_id, week_index] <= 1 - selected[player_id, week_index])
            model.add(incoming[player_id, week_index] + outgoing[player_id, week_index] <= 1)
            original_before = 1 if week_index == 0 and player_id in current_ids else 0 if week_index == 0 else original_holding[player_id, week_index - 1]
            if week_index == 0:
                model.add(original_holding[player_id, week_index] == (selected[player_id, week_index] if player_id in current_ids else 0))
            else:
                model.add(original_holding[player_id, week_index] <= original_before)
                model.add(original_holding[player_id, week_index] <= selected[player_id, week_index])
                model.add(original_holding[player_id, week_index] >= original_before + selected[player_id, week_index] - 1)
            model.add(original_outgoing[player_id, week_index] <= outgoing[player_id, week_index])
            model.add(original_outgoing[player_id, week_index] <= original_before)
            model.add(original_outgoing[player_id, week_index] >= outgoing[player_id, week_index] + original_before - 1)

        model.add(sum(selected[player_id, week_index] for player_id in player_ids) == 15)
        for position, required in {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}.items():
            model.add(sum(selected[player_id, week_index] for player_id in player_ids if metadata[player_id].position == position) == required)
        keys = {club_key(metadata[player_id].club) for player_id in player_ids}
        for key in keys:
            model.add(sum(selected[player_id, week_index] for player_id in player_ids if club_key(metadata[player_id].club) == key) <= 3)
        model.add(sum(starter[player_id, week_index] for player_id in player_ids) == 11)
        model.add(sum(captain[player_id, week_index] for player_id in player_ids) == 1)
        model.add(sum(starter[player_id, week_index] for player_id in player_ids if metadata[player_id].position == "GK") == 1)
        for position, minimum, maximum in (("DEF", 3, 5), ("MID", 2, 5), ("FWD", 1, 3)):
            count = sum(starter[player_id, week_index] for player_id in player_ids if metadata[player_id].position == position)
            model.add(count >= minimum)
            model.add(count <= maximum)

        unlimited = pre_season and week_index == 0
        transfers = model.new_int_var(0, 15, f"transfers_{gameweek}")
        model.add(transfers == sum(incoming[player_id, week_index] for player_id in player_ids))
        if current_ids or week_index > 0:
            model.add(transfers == sum(outgoing[player_id, week_index] for player_id in player_ids))
        transfer_counts.append(transfers)
        free = model.new_int_var(1, 5, f"free_transfers_{gameweek}")
        free_transfers.append(free)
        if week_index == 0:
            model.add(free == initial_free_transfers)
        elif pre_season and week_index == 1:
            # Opening-squad transfers expire at the GW1 deadline.
            model.add(free == 1)
        else:
            remaining = model.new_int_var(0, 15, f"remaining_free_{gameweeks[week_index - 1]}")
            model.add_max_equality(remaining, [free_transfers[week_index - 1] - transfer_counts[week_index - 1], 0])
            model.add_min_equality(free, [remaining + 1, 5])
        if not unlimited:
            # Free transfers are never suppressed by the paid-transfer safety cap.
            model.add(transfers <= free + paid_transfer_cap)
        excess = model.new_int_var(0, 15, f"excess_transfers_{gameweek}")
        model.add_max_equality(excess, [transfers - free, 0])
        excess_transfers.append(excess)

        bank = model.new_int_var(0, 5000, f"bank_{gameweek}")
        previous_bank = state.bank if week_index == 0 else banks[week_index - 1]
        initial_funds = DEFAULT_BUDGET_TENTHS if (week_index == 0 and not current_ids) else 0
        sale_value = sum(
            original_outgoing[player_id, week_index] * sale_values.get(player_id, metadata[player_id].cost)
            + (outgoing[player_id, week_index] - original_outgoing[player_id, week_index]) * metadata[player_id].cost
            for player_id in player_ids
        )
        purchase_cost = sum(incoming[player_id, week_index] * metadata[player_id].cost for player_id in player_ids)
        model.add(bank == previous_bank + initial_funds + sale_value - purchase_cost)
        banks.append(bank)
        shortfall = model.new_int_var(0, max(1, min_bank), f"bank_shortfall_{gameweek}")
        model.add_max_equality(shortfall, [min_bank - bank, 0])
        bank_shortfalls.append(shortfall)
        excess = model.new_int_var(0, 4, f"dead_bench_excess_{gameweek}")
        model.add_max_equality(
            excess,
            [
                sum(dead_bench[player_id, week_index] for player_id in player_ids)
                - DEAD_BENCH_ALLOWANCE,
                0,
            ],
        )
        dead_bench_excess.append(excess)

    free_by_week = free_transfers

    # Seed a legal hold strategy so a time-limited solve never returns a plan worse than doing nothing.
    held_free_transfers = initial_free_transfers
    initial_hints: dict[int, tuple[set[int], set[int], int]] = {}
    if not current_ids:
        week_one = [row for row in rows if row.gameweek == gameweeks[0]]
        opening = optimize_squad(
            _aggregate(week_one), preferred_player_ids=preferred_player_ids,
            differential_appetite=differential_appetite,
        )
        initial_hints[0] = (
            {player.player_id for player in opening.players},
            {player.player_id for player in opening.starting_xi},
            opening.captain.player_id,
        )
    for week_index, gameweek in enumerate(gameweeks):
        if not current_ids:
            if week_index in initial_hints:
                hinted, hinted_starters, hinted_captain = initial_hints[week_index]
                for player_id in player_ids:
                    model.add_hint(selected[player_id, week_index], 1 if player_id in hinted else 0)
                    model.add_hint(starter[player_id, week_index], 1 if player_id in hinted_starters else 0)
                    model.add_hint(captain[player_id, week_index], 1 if player_id == hinted_captain else 0)
            continue
        held_players = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in current_ids]
        held_lineup = select_best_lineup(SquadRequest(
            players=[_squad_player(player) for player in held_players],
            budget=sum(player.cost for player in held_players) + state.bank,
        ))
        starter_ids = {player.id for player in held_lineup.starters}
        for player_id in player_ids:
            model.add_hint(selected[player_id, week_index], 1 if player_id in current_ids else 0)
            model.add_hint(starter[player_id, week_index], 1 if player_id in starter_ids else 0)
            model.add_hint(captain[player_id, week_index], 1 if player_id == held_lineup.captain.id else 0)
            model.add_hint(incoming[player_id, week_index], 0)
            model.add_hint(outgoing[player_id, week_index], 0)
            model.add_hint(original_holding[player_id, week_index], 1 if player_id in current_ids else 0)
            model.add_hint(original_outgoing[player_id, week_index], 0)
        model.add_hint(transfer_counts[week_index], 0)
        model.add_hint(free_by_week[week_index], held_free_transfers)
        model.add_hint(excess_transfers[week_index], 0)
        model.add_hint(banks[week_index], state.bank)
        held_free_transfers = (
            1 if pre_season and week_index == 0
            else min(5, max(0, held_free_transfers) + 1)
        )

    objective = []
    for week_index, gameweek in enumerate(gameweeks):
        week_weight = week_weights[week_index].week_weight
        unlimited = pre_season and week_index == 0
        objective.extend(
            starter[player_id, week_index]
            * starter_coefficient(by_player_gameweek[player_id, gameweek].projected_points, week_weight)
            for player_id in player_ids
        )
        objective.extend(
            captain[player_id, week_index]
            * captain_coefficient(by_player_gameweek[player_id, gameweek].projected_points, week_weight)
            for player_id in player_ids
        )
        objective.extend(
            bench[player_id, week_index]
            * bench_coefficient(
                by_player_gameweek[player_id, gameweek].projected_points,
                week_weight,
                objective_settings.bench_weight,
            )
            for player_id in player_ids
        )
        objective.append(
            -excess_transfers[week_index]
            * strategy_hit_coefficient(week_weight, objective_settings, unlimited)
        )
        objective.append(
            -transfer_counts[week_index] * churn_coefficient(week_weight, objective_settings)
        )
        objective.append(
            -bank_shortfalls[week_index]
            * bank_shortfall_coefficient(week_weight, objective_settings)
        )
        objective.append(
            -dead_bench_excess[week_index]
            * dead_bench_coefficient(week_weight, objective_settings)
        )
        objective.extend(
            selected[player_id, week_index] * preferred_coefficient(week_weight)
            for player_id in preferred_player_ids or set() if player_id in player_ids
        )
        objective.extend(
            selected[player_id, week_index]
            * differential_coefficient(
                by_player_gameweek[player_id, gameweek].projected_points,
                by_player_gameweek[player_id, gameweek].selected_by_percent,
                differential_appetite,
                week_weight,
            )
            for player_id in player_ids
        )
    model.maximize(sum(objective))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.max_time_in_seconds = float(os.environ.get("AIFPL_HORIZON_SOLVER_MAX_SECONDS", "60"))
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SquadOptimizationError("No legal multi-gameweek transfer plan exists")

    plans: list[HorizonGameweekPlan] = []
    purchase_book = dict(purchase_prices)
    account_bank = state.bank
    for week_index, gameweek in enumerate(gameweeks):
        squad = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in player_ids if solver.value(selected[player_id, week_index])]
        incoming_players = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in player_ids if solver.value(incoming[player_id, week_index])]
        outgoing_players = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in player_ids if solver.value(outgoing[player_id, week_index])]
        players_by_id = {player.player_id: player for player in squad}
        lineup = [
            players_by_id[player_id]
            for player_id in player_ids
            if solver.value(starter[player_id, week_index])
        ]
        if len(lineup) != 11 or not any(
            solver.value(captain[player_id, week_index]) for player_id in player_ids
        ):
            raise SquadOptimizationError("Solver returned an invalid lineup")
        # Stable tie-breaking keeps persistence deterministic while preserving
        # the quantized captain term used by the model.
        captain_player = min(lineup, key=lambda player: (-player.projected_points, player.player_id))
        projected = sum(player.projected_points for player in lineup) + captain_player.projected_points
        rows_by_id = {row.player_id: row for row in rows if row.gameweek == gameweek}
        starting_ids = {player.player_id for player in lineup}
        unlimited = pre_season and week_index == 0
        free_before = solver.value(free_by_week[week_index])
        transfers_made = solver.value(transfer_counts[week_index])
        free_after = (
            1
            if unlimited
            else min(5, max(0, free_before - transfers_made) + 1)
        )
        bank_before = account_bank
        sale_value = 0
        for player in outgoing_players:
            purchase_price = purchase_book.pop(player.player_id, None)
            if purchase_price is None:
                raise SquadOptimizationError("Solver returned a transition with an unknown purchase price")
            sale_value += (
                player.cost
                if player.cost <= purchase_price
                else purchase_price + (player.cost - purchase_price) // 2
            )
        purchase_value = 0
        for player in incoming_players:
            if player.player_id in purchase_book:
                raise SquadOptimizationError("Solver returned a purchase of a player already held")
            purchase_book[player.player_id] = player.cost
            purchase_value += player.cost
        expected_bank = bank_before + (
            DEFAULT_BUDGET_TENTHS if week_index == 0 and not current_ids else 0
        ) + sale_value - purchase_value
        bank_after = solver.value(banks[week_index])
        if bank_after != expected_bank:
            raise SquadOptimizationError("Solver returned inconsistent bank accounting")
        hit = 0 if unlimited else max(0, transfers_made - free_before) * 4
        breakdown = horizon_objective_breakdown(
            rows_by_id=rows_by_id,
            selected_ids={player.player_id for player in squad},
            starter_ids=starting_ids,
            captain_id=captain_player.player_id,
            transfers_made=transfers_made,
            free_transfers_before=free_before,
            bank_after=bank_after,
            bench_floor=bench_floor,
            preferred_player_ids=preferred_player_ids,
            differential_appetite=differential_appetite,
            week=week_weights[week_index],
            settings=objective_settings,
            unlimited_transfers=unlimited,
        )
        plans.append(HorizonGameweekPlan(
            gameweek=gameweek, outgoing=_sort(outgoing_players), incoming=_sort(incoming_players),
            resulting_squad=_sort(squad), starting_xi=_sort(lineup), captain=captain_player,
            vice_captain=_second_best(lineup, captain_player.player_id),
            transfers_made=transfers_made,
            free_transfers_before=free_before, hit_cost=hit,
            bank_after=bank_after, projected_points=round(projected, 4),
            net_projected_points=round(projected - hit, 4),
            objective_net_points=round(breakdown.total, 4),
            robustness_score=_robustness_score(
                squad, starting_ids, bank_after, transfers_made, rows_by_id, bench_floor, bank_scale,
            ),
            unlimited_transfers=unlimited,
            free_transfers_after=free_after,
            odds_coverage=round(week_weights[week_index].odds_coverage, 4),
            bank_before=bank_before,
            purchase_value=purchase_value,
            sale_value=sale_value,
            objective_components=breakdown.as_dict(),
        ))
        account_bank = bank_after
    solver_objective = solver.objective_value
    if callable(solver_objective):
        solver_objective = solver_objective()
    model_objective = round(float(solver_objective))
    reported_objective = round(sum(plan.objective_net_points for plan in plans) * OBJECTIVE_SCALE)
    if abs(model_objective - reported_objective) > 1:
        raise SquadOptimizationError("Solver objective and reported objective differ")
    hold_plans = _hold_plans(
        rows, state, gameweeks, by_player_gameweek, current_ids, pre_season,
        bench_floor, bank_scale, objective_settings, week_weights,
        preferred_player_ids, differential_appetite,
    ) if current_ids else []
    if pre_season and hold_plans and _hold_plans_violate_robustness(hold_plans, min_bank, bench_floor, by_player_gameweek):
        hold_plans = []
    if hold_plans and sum(plan.objective_net_points for plan in plans) < sum(plan.objective_net_points for plan in hold_plans):
        plans = hold_plans
        solver_status = "HOLD_FALLBACK"
    else:
        solver_status = solver.status_name(status)
    result = HorizonTransferPlan(
        gameweeks=plans, total_projected_points=round(sum(plan.projected_points for plan in plans), 4),
        total_hit_cost=sum(plan.hit_cost for plan in plans),
        total_net_projected_points=round(sum(plan.net_projected_points for plan in plans), 4),
        solver_status=solver_status, methodology=rows[0].methodology,
        robustness_score=round(sum(plan.robustness_score for plan in plans) / max(1, len(plans)), 1),
        objective_value=round(sum(plan.objective_net_points for plan in plans), 4),
        objective_components=aggregate_objective_components(
            [plan.objective_components for plan in plans]
        ),
    )
    try:
        validate_horizon_plan(
            result, rows, state, pre_season=pre_season,
            decision_hit_penalty=decision_hit_penalty,
            churn_penalty=churn_penalty,
            preferred_player_ids=preferred_player_ids,
            differential_appetite=differential_appetite,
            bench_floor=bench_floor,
            paid_transfer_cap=paid_transfer_cap,
            settings=objective_settings,
            initial_purchase_prices=purchase_prices,
        )
    except ValueError as exc:
        raise SquadOptimizationError(f"Post-solve horizon validation failed: {exc}") from exc
    return result


def plan_hold_horizon_transfers(
    rows: list[OddsAdjustedGameweekProjection], state: HorizonSquadState,
    decision_hit_penalty: float = 4.0,
    preferred_player_ids: set[int] | None = None,
    differential_appetite: float = 0.0,
    pre_season: bool = False,
    churn_penalty: float | None = None,
) -> HorizonTransferPlan:
    """Build the complete no-transfer counterfactual for a current squad."""
    return plan_horizon_transfers(
        rows,
        state,
        decision_hit_penalty=decision_hit_penalty,
        preferred_player_ids=preferred_player_ids,
        differential_appetite=differential_appetite,
        pre_season=pre_season,
        churn_penalty=churn_penalty,
        hold_only=True,
    )


def _hold_plans_violate_robustness(
    hold_plans: list[HorizonGameweekPlan], min_bank: int, bench_floor: float,
    by_player_gameweek: dict[tuple[int, int], OddsAdjustedGameweekProjection],
) -> bool:
    for plan in hold_plans:
        if plan.bank_after < min_bank:
            return True
        xi_ids = {player.player_id for player in plan.starting_xi}
        dead = sum(
            1
            for player in plan.resulting_squad
            if player.player_id not in xi_ids
            and by_player_gameweek[(player.player_id, plan.gameweek)].projected_points < bench_floor
        )
        if dead > DEAD_BENCH_ALLOWANCE:
            return True
    return False


def _hold_plans(
    rows: list[OddsAdjustedGameweekProjection], state: HorizonSquadState, gameweeks: list[int],
    by_player_gameweek: dict[tuple[int, int], OddsAdjustedGameweekProjection], current_ids: set[int],
    pre_season: bool, bench_floor: float, bank_scale: int,
    objective_settings: HorizonObjectiveSettings, week_weights: list[HorizonWeekWeight],
    preferred_player_ids: set[int] | None, differential_appetite: float,
) -> list[HorizonGameweekPlan]:
    plans: list[HorizonGameweekPlan] = []
    free = 5 if pre_season else state.free_transfers
    for week_index, gameweek in enumerate(gameweeks):
        squad = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in current_ids]
        by_id = {player.player_id: player for player in squad}
        rows_by_id = {row.player_id: row for row in rows if row.gameweek == gameweek}
        starter_ids, captain_id = best_objective_lineup(
            squad, rows_by_id, week_weights[week_index], objective_settings, bench_floor,
        )
        starters = [by_id[player_id] for player_id in starter_ids]
        captain = by_id[captain_id]
        starting_ids = {player.player_id for player in starters}
        unlimited = pre_season and week_index == 0
        free_after = 1 if unlimited else min(5, free + 1)
        breakdown = horizon_objective_breakdown(
            rows_by_id=rows_by_id,
            selected_ids=set(current_ids),
            starter_ids=starting_ids,
            captain_id=captain.player_id,
            transfers_made=0,
            free_transfers_before=free,
            bank_after=state.bank,
            bench_floor=bench_floor,
            preferred_player_ids=preferred_player_ids,
            differential_appetite=differential_appetite,
            week=week_weights[week_index],
            settings=objective_settings,
            unlimited_transfers=unlimited,
        )
        plans.append(HorizonGameweekPlan(
            gameweek=gameweek, outgoing=[], incoming=[], resulting_squad=_sort(squad),
            starting_xi=_sort(starters), captain=captain, vice_captain=_second_best(starters, captain.player_id),
            transfers_made=0,
            free_transfers_before=free, hit_cost=0, bank_after=state.bank,
            projected_points=round(sum(player.projected_points for player in starters) + captain.projected_points, 4),
            net_projected_points=round(sum(player.projected_points for player in starters) + captain.projected_points, 4),
            objective_net_points=round(breakdown.total, 4),
            robustness_score=_robustness_score(
                squad, starting_ids, state.bank, 0, rows_by_id, bench_floor, bank_scale,
            ),
            unlimited_transfers=unlimited,
            free_transfers_after=free_after,
            odds_coverage=round(week_weights[week_index].odds_coverage, 4),
            bank_before=state.bank,
            purchase_value=0,
            sale_value=0,
            objective_components=breakdown.as_dict(),
        ))
        free = 1 if pre_season and week_index == 0 else min(5, free + 1)
    return plans


def _robustness_score(
    squad: list[CurrentPlayerProjection],
    starting_ids: set[int],
    bank_after: int,
    transfers_made: int,
    rows_by_id: dict[int, OddsAdjustedGameweekProjection],
    bench_floor: float,
    bank_scale: int,
) -> float:
    xi = [player for player in squad if player.player_id in starting_ids]
    minutes = [rows_by_id[player.player_id].expected_minutes for player in xi if player.player_id in rows_by_id]
    minutes_factor = sum((value / 90 if value is not None else 0.8 for value in minutes)) / max(1, len(minutes))
    bench = [player for player in squad if player.player_id not in starting_ids]
    bench_strength = sum(
        1 for player in bench
        if player.player_id in rows_by_id and rows_by_id[player.player_id].projected_points >= bench_floor
    ) / max(1, len(bench))
    flexibility = min(1.0, max(0, bank_after) / max(1, bank_scale))
    rotation_risk = sum(1 for value in minutes if value is not None and value < 60) / max(1, len(minutes))
    churn = min(1.0, transfers_made / 5)
    return round(
        max(0.0, 100 * (
            0.35 * minutes_factor + 0.25 * bench_strength + 0.20 * flexibility
            + 0.20 * (1 - rotation_risk) - 0.10 * churn
        )),
        1,
    )


def _candidate(row: OddsAdjustedGameweekProjection, gameweek: int) -> CurrentPlayerProjection:
    return CurrentPlayerProjection(
        player_id=row.player_id, player_name=row.player_name, position=row.position,
        club=row.club, cost=row.cost, projected_points=row.projected_points,
        availability_multiplier=1.0, methodology=row.methodology,
    )


def _squad_player(player: CurrentPlayerProjection) -> SquadPlayer:
    return SquadPlayer(
        id=player.player_id, name=player.player_name, position=player.position,
        club=player.club, cost=player.cost, projected_points=player.projected_points,
    )


def _sort(players: list[CurrentPlayerProjection]) -> list[CurrentPlayerProjection]:
    return sorted(players, key=lambda player: (("GK", "DEF", "MID", "FWD").index(player.position), player.player_id))


def _second_best(lineup: list[CurrentPlayerProjection], captain_id: int) -> CurrentPlayerProjection | None:
    others = [player for player in lineup if player.player_id != captain_id]
    return min(others, key=lambda player: (-player.projected_points, player.player_id), default=None)
