from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

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
    """Find the exact highest-projected legal squad from every supplied candidate."""
    if budget < 0:
        raise ValueError("budget must not be negative")
    if not 0 <= differential_appetite <= 1:
        raise ValueError("differential_appetite must be within 0..1")
    if len({candidate.player_id for candidate in candidates}) != len(candidates):
        raise ValueError("Candidate player IDs must be unique")
    model = cp_model.CpModel()
    selected = [model.new_bool_var(f"player_{candidate.player_id}") for candidate in candidates]
    starters = [model.new_bool_var(f"starter_{candidate.player_id}") for candidate in candidates]
    captains = [model.new_bool_var(f"captain_{candidate.player_id}") for candidate in candidates]
    position_requirements = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
    for position, required in position_requirements.items():
        model.add(sum(selected[index] for index, candidate in enumerate(candidates) if candidate.position == position) == required)
    club_keys = [club_key(candidate.club) for candidate in candidates]
    for club in set(club_keys):
        model.add(sum(selected[index] for index, key in enumerate(club_keys) if key == club) <= 3)
    for index in range(len(candidates)):
        model.add(starters[index] <= selected[index])
        model.add(captains[index] <= starters[index])
    model.add(sum(starters) == 11)
    model.add(sum(captains) == 1)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "GK") == 1)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "DEF") >= 3)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "DEF") <= 5)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "MID") >= 2)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "MID") <= 5)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "FWD") >= 1)
    model.add(sum(starters[index] for index, candidate in enumerate(candidates) if candidate.position == "FWD") <= 3)
    model.add(sum(selected[index] * candidate.cost for index, candidate in enumerate(candidates)) <= budget)
    model.maximize(
        sum(starters[index] * round(candidate.projected_points * 10_000) for index, candidate in enumerate(candidates))
        + sum(captains[index] * round(candidate.projected_points * 10_000) for index, candidate in enumerate(candidates))
        + sum(selected[index] * 100 for index, candidate in enumerate(candidates) if candidate.player_id in (preferred_player_ids or set()))
        + sum(starters[index] * round((100 - candidate.selected_by_percent) * differential_appetite) for index, candidate in enumerate(candidates))
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
