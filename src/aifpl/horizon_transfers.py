from __future__ import annotations

import os
from dataclasses import dataclass

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field

from aifpl.current_projections import CurrentPlayerProjection
from aifpl.odds_projections import OddsAdjustedGameweekProjection
from aifpl.optimizer import SquadOptimizationError, optimize_squad
from aifpl.projection_catalogs import _aggregate
from aifpl.rules import DEFAULT_BUDGET_TENTHS, SquadPlayer, SquadRequest, club_key, select_best_lineup, validate_squad


class HorizonSquadState(BaseModel):
    player_ids: list[int] = Field(min_length=0, max_length=15)
    bank: int = Field(ge=0, le=5000)
    free_transfers: int = Field(ge=0, le=5)
    purchase_prices: dict[int, int] | None = None


@dataclass(frozen=True)
class HorizonGameweekPlan:
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


@dataclass(frozen=True)
class HorizonTransferPlan:
    gameweeks: list[HorizonGameweekPlan]
    total_projected_points: float
    total_hit_cost: int
    total_net_projected_points: float
    solver_status: str
    methodology: str


def plan_horizon_transfers(
    rows: list[OddsAdjustedGameweekProjection], state: HorizonSquadState,
    decision_hit_penalty: float = 4.0,
    preferred_player_ids: set[int] | None = None,
    differential_appetite: float = 0.0,
) -> HorizonTransferPlan:
    if decision_hit_penalty < 4:
        raise ValueError("Decision hit penalty cannot be lower than the actual four-point hit")
    if not 0 <= differential_appetite <= 1:
        raise ValueError("differential_appetite must be within 0..1")
    gameweeks = sorted({row.gameweek for row in rows})
    if not 1 <= len(gameweeks) <= 6 or gameweeks != list(range(gameweeks[0], gameweeks[-1] + 1)):
        raise ValueError("Horizon projection catalog must contain 1 to 6 contiguous gameweeks")
    by_player_gameweek = {(row.player_id, row.gameweek): row for row in rows}
    if len(by_player_gameweek) != len(rows):
        raise ValueError("Projection catalog contains duplicate player-gameweek rows")
    player_ids = sorted({row.player_id for row in rows})
    if any((player_id, gameweek) not in by_player_gameweek for player_id in player_ids for gameweek in gameweeks):
        raise ValueError("Projection catalog has an incomplete player-gameweek horizon")
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

    model = cp_model.CpModel()
    selected: dict[tuple[int, int], cp_model.IntVar] = {}
    starter: dict[tuple[int, int], cp_model.IntVar] = {}
    captain: dict[tuple[int, int], cp_model.IntVar] = {}
    incoming: dict[tuple[int, int], cp_model.IntVar] = {}
    outgoing: dict[tuple[int, int], cp_model.IntVar] = {}
    original_holding: dict[tuple[int, int], cp_model.IntVar] = {}
    original_outgoing: dict[tuple[int, int], cp_model.IntVar] = {}
    transfer_counts: list[cp_model.IntVar] = []
    free_transfers: list[cp_model.IntVar] = []
    excess_transfers: list[cp_model.IntVar] = []
    banks: list[cp_model.IntVar] = []
    scale = 10_000

    for week_index, gameweek in enumerate(gameweeks):
        for player_id in player_ids:
            selected[player_id, week_index] = model.new_bool_var(f"selected_{player_id}_{gameweek}")
            starter[player_id, week_index] = model.new_bool_var(f"starter_{player_id}_{gameweek}")
            captain[player_id, week_index] = model.new_bool_var(f"captain_{player_id}_{gameweek}")
            incoming[player_id, week_index] = model.new_bool_var(f"in_{player_id}_{gameweek}")
            outgoing[player_id, week_index] = model.new_bool_var(f"out_{player_id}_{gameweek}")
            original_holding[player_id, week_index] = model.new_bool_var(f"original_{player_id}_{gameweek}")
            original_outgoing[player_id, week_index] = model.new_bool_var(f"original_out_{player_id}_{gameweek}")
            model.add(starter[player_id, week_index] <= selected[player_id, week_index])
            model.add(captain[player_id, week_index] <= starter[player_id, week_index])

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

        transfers = model.new_int_var(0, 15, f"transfers_{gameweek}")
        model.add(transfers == sum(incoming[player_id, week_index] for player_id in player_ids))
        if current_ids or week_index > 0:
            model.add(transfers == sum(outgoing[player_id, week_index] for player_id in player_ids))
        transfer_counts.append(transfers)
        free = model.new_int_var(0, 5, f"free_transfers_{gameweek}")
        free_transfers.append(free)
        if week_index == 0:
            model.add(free == state.free_transfers)
        excess = model.new_int_var(0, 15, f"excess_transfers_{gameweek}")
        model.add_max_equality(excess, [transfers - free, 0])
        excess_transfers.append(excess)
        if week_index + 1 < len(gameweeks):
            remaining = model.new_int_var(0, 5, f"remaining_free_{gameweek}")
            model.add_max_equality(remaining, [free - transfers, 0])
            accrued = model.new_int_var(1, 6, f"accrued_free_{gameweek}")
            model.add(accrued == remaining + 1)
            next_free = model.new_int_var(0, 5, f"free_transfers_{gameweeks[week_index + 1]}")
            model.add_min_equality(next_free, [accrued, 5])
            # Reuse this variable when the next week is constructed.
            free_transfers.append(next_free)

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

    # Rebuild free-transfer rollover constraints without duplicate week variables.
    for week_index in range(1, len(gameweeks)):
        # The list contains both current and pre-created variables; bind the current variable to the rollover variable.
        rollover = free_transfers[week_index * 2 - 1]
        current = free_transfers[week_index * 2] if week_index * 2 < len(free_transfers) else free_transfers[-1]
        model.add(current == rollover)
    free_by_week = [free_transfers[0]] + [free_transfers[index * 2] for index in range(1, len(gameweeks))]

    # Seed a legal hold strategy so a time-limited solve never returns a plan worse than doing nothing.
    held_free_transfers = state.free_transfers
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
        held_free_transfers = min(5, held_free_transfers + 1)

    objective = []
    for week_index, gameweek in enumerate(gameweeks):
        objective.extend(
            starter[player_id, week_index] * round(by_player_gameweek[player_id, gameweek].projected_points * scale)
            for player_id in player_ids
        )
        objective.extend(
            captain[player_id, week_index] * round(by_player_gameweek[player_id, gameweek].projected_points * scale)
            for player_id in player_ids
        )
        objective.append(-excess_transfers[week_index] * round(decision_hit_penalty * scale))
        objective.append(-transfer_counts[week_index])
        if preferred_player_ids:
            objective.extend(selected[player_id, week_index] * 100 for player_id in preferred_player_ids if player_id in player_ids)
        objective.extend(
            starter[player_id, week_index] * round((100 - metadata[player_id].selected_by_percent) * differential_appetite)
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
    for week_index, gameweek in enumerate(gameweeks):
        squad = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in player_ids if solver.value(selected[player_id, week_index])]
        lineup = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in player_ids if solver.value(starter[player_id, week_index])]
        captain_player = next(_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in player_ids if solver.value(captain[player_id, week_index]))
        incoming_players = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in player_ids if solver.value(incoming[player_id, week_index])]
        outgoing_players = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in player_ids if solver.value(outgoing[player_id, week_index])]
        projected = sum(player.projected_points for player in lineup) + captain_player.projected_points
        hit = solver.value(excess_transfers[week_index]) * 4
        fixture_total = sum(row.fixture_count for row in rows if row.gameweek == gameweek)
        odds_total = sum(row.odds_backed_fixture_count for row in rows if row.gameweek == gameweek)
        plans.append(HorizonGameweekPlan(
            gameweek=gameweek, outgoing=_sort(outgoing_players), incoming=_sort(incoming_players),
            resulting_squad=_sort(squad), starting_xi=_sort(lineup), captain=captain_player,
            transfers_made=solver.value(transfer_counts[week_index]),
            free_transfers_before=solver.value(free_by_week[week_index]), hit_cost=hit,
            bank_after=solver.value(banks[week_index]), projected_points=round(projected, 4),
            net_projected_points=round(projected - hit, 4),
            odds_coverage=round(odds_total / fixture_total, 4) if fixture_total else 0.0,
        ))
    hold_plans = _hold_plans(rows, state, gameweeks, by_player_gameweek, current_ids) if current_ids else []
    if sum(plan.net_projected_points for plan in plans) < sum(plan.net_projected_points for plan in hold_plans):
        plans = hold_plans
        solver_status = "HOLD_FALLBACK"
    else:
        solver_status = solver.status_name(status)
    return HorizonTransferPlan(
        gameweeks=plans, total_projected_points=round(sum(plan.projected_points for plan in plans), 4),
        total_hit_cost=sum(plan.hit_cost for plan in plans),
        total_net_projected_points=round(sum(plan.net_projected_points for plan in plans), 4),
        solver_status=solver_status, methodology=rows[0].methodology,
    )


def _hold_plans(
    rows: list[OddsAdjustedGameweekProjection], state: HorizonSquadState, gameweeks: list[int],
    by_player_gameweek: dict[tuple[int, int], OddsAdjustedGameweekProjection], current_ids: set[int],
) -> list[HorizonGameweekPlan]:
    plans: list[HorizonGameweekPlan] = []
    free = state.free_transfers
    for gameweek in gameweeks:
        squad = [_candidate(by_player_gameweek[player_id, gameweek], gameweek) for player_id in current_ids]
        lineup = select_best_lineup(SquadRequest(
            players=[_squad_player(player) for player in squad],
            budget=sum(player.cost for player in squad) + state.bank,
        ))
        by_id = {player.player_id: player for player in squad}
        starters = [by_id[player.id] for player in lineup.starters]
        captain = by_id[lineup.captain.id]
        fixture_total = sum(row.fixture_count for row in rows if row.gameweek == gameweek)
        odds_total = sum(row.odds_backed_fixture_count for row in rows if row.gameweek == gameweek)
        plans.append(HorizonGameweekPlan(
            gameweek=gameweek, outgoing=[], incoming=[], resulting_squad=_sort(squad),
            starting_xi=_sort(starters), captain=captain, transfers_made=0,
            free_transfers_before=free, hit_cost=0, bank_after=state.bank,
            projected_points=lineup.projected_points, net_projected_points=lineup.projected_points,
            odds_coverage=round(odds_total / fixture_total, 4) if fixture_total else 0.0,
        ))
        free = min(5, free + 1)
    return plans


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
