from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from aifpl.config import bench_min_projection, bench_weight, minimum_bank_tenths
from aifpl.current_projections import CurrentPlayerProjection
from aifpl.rules import DEFAULT_BUDGET_TENTHS, SquadPlayer, SquadRequest, club_key, validate_squad


class SquadOptimizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OptimizedSquad:
    players: list[CurrentPlayerProjection]
    total_cost: int
    bank: int
    projected_points: float
    budget: int
    solver_status: str
    methodology: str
    starting_xi: list[CurrentPlayerProjection]
    captain: CurrentPlayerProjection


def optimize_squad(
    candidates: list[CurrentPlayerProjection], budget: int = DEFAULT_BUDGET_TENTHS,
    preferred_player_ids: set[int] | None = None,
    differential_appetite: float = 0.0,
) -> OptimizedSquad:
    """Find the exact highest-projected legal squad from every supplied candidate.

    differential_appetite in 0..1 tilts squad purchases toward under-owned players:
    each selected player earns up to appetite * projected_points * (1 - ownership/100)
    extra objective value, so it never overrides a positive projection. The starting
    XI is always the highest-projected legal lineup; appetite does not affect it.
    """
    if budget < 0:
        raise ValueError("budget must not be negative")
    if not 0 <= differential_appetite <= 1:
        raise ValueError("differential_appetite must be within 0..1")
    if len({candidate.player_id for candidate in candidates}) != len(candidates):
        raise ValueError("Candidate player IDs must be unique")
    min_bank = minimum_bank_tenths()
    floor = bench_min_projection()
    bench_bonus = bench_weight()
    if budget < min_bank:
        raise ValueError("budget must be at least the minimum bank reserve")
    model = cp_model.CpModel()
    selected = [model.new_bool_var(f"player_{candidate.player_id}") for candidate in candidates]
    starters = [model.new_bool_var(f"starter_{candidate.player_id}") for candidate in candidates]
    captains = [model.new_bool_var(f"captain_{candidate.player_id}") for candidate in candidates]
    benches = [model.new_bool_var(f"bench_{candidate.player_id}") for candidate in candidates]
    dead_bench = [model.new_bool_var(f"dead_bench_{candidate.player_id}") for candidate in candidates]
    position_requirements = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
    for position, required in position_requirements.items():
        model.add(sum(selected[index] for index, candidate in enumerate(candidates) if candidate.position == position) == required)
    club_keys = [club_key(candidate.club) for candidate in candidates]
    for club in set(club_keys):
        model.add(sum(selected[index] for index, key in enumerate(club_keys) if key == club) <= 3)
    for index in range(len(candidates)):
        model.add(starters[index] <= selected[index])
        model.add(captains[index] <= starters[index])
        model.add(selected[index] == starters[index] + benches[index])
        model.add(dead_bench[index] <= benches[index])
        model.add(candidates[index].projected_points >= floor).only_enforce_if(selected[index], benches[index], dead_bench[index].Not())
    model.add(sum(dead_bench) <= 2)
    model.add(sum(starters) == 11)
    model.add(sum(captains) == 1)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "GK") == 1)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "DEF") >= 3)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "DEF") <= 5)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "MID") >= 2)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "MID") <= 5)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "FWD") >= 1)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "FWD") <= 3)
    model.add(sum(selected[index] * candidate.cost for index, candidate in enumerate(candidates)) <= budget - min_bank)
    model.maximize(
        sum(starters[index] * round(candidate.projected_points * 10_000) for index, candidate in enumerate(candidates))
        + sum(captains[index] * round(candidate.projected_points * 10_000) for index, candidate in enumerate(candidates))
        + sum(benches[index] * round(candidate.projected_points * bench_bonus * 10_000) for index, candidate in enumerate(candidates))
        + sum(selected[index] * 100 for index, candidate in enumerate(candidates) if candidate.player_id in (preferred_player_ids or set()))
        + sum(
            selected[index]
            * round(candidate.projected_points * differential_appetite * (100 - candidate.selected_by_percent) / 100 * 10_000)
            for index, candidate in enumerate(candidates)
        )
    )
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SquadOptimizationError("No legal squad can be built from the candidate pool within the budget")
    squad = [candidate for index, candidate in enumerate(candidates) if solver.value(selected[index])]
    starting_xi = [candidate for index, candidate in enumerate(candidates) if solver.value(starters[index])]
    captain = next(candidate for index, candidate in enumerate(candidates) if solver.value(captains[index]))
    squad.sort(key=lambda candidate: (candidate.position, candidate.player_id))
    validation = validate_squad(SquadRequest(players=[
        SquadPlayer(
            id=candidate.player_id, name=candidate.player_name, position=candidate.position,
            club=candidate.club, cost=candidate.cost, projected_points=candidate.projected_points,
        )
        for candidate in squad
    ], budget=budget))
    if not validation.legal:
        raise SquadOptimizationError("Solver returned an invalid squad: " + "; ".join(validation.errors))
    total_cost = sum(candidate.cost for candidate in squad)
    return OptimizedSquad(
        players=squad,
        total_cost=total_cost,
        bank=budget - total_cost,
        projected_points=round(sum(candidate.projected_points for candidate in starting_xi) + captain.projected_points, 4),
        budget=budget,
        solver_status=solver.status_name(status),
        methodology=squad[0].methodology if squad else "unknown",
        starting_xi=sorted(starting_xi, key=lambda candidate: (("GK", "DEF", "MID", "FWD").index(candidate.position), candidate.player_id)),
        captain=captain,
    )
