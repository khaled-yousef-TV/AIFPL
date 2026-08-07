from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from aifpl.current_projections import CurrentPlayerProjection
from aifpl.rules import DEFAULT_BUDGET_TENTHS, SquadPlayer, SquadRequest, validate_squad


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


def optimize_squad(
    candidates: list[CurrentPlayerProjection], budget: int = DEFAULT_BUDGET_TENTHS
) -> OptimizedSquad:
    """Find the exact highest-projected legal squad from every supplied candidate."""
    if budget < 0:
        raise ValueError("budget must not be negative")
    if len({candidate.player_id for candidate in candidates}) != len(candidates):
        raise ValueError("Candidate player IDs must be unique")
    model = cp_model.CpModel()
    selected = [model.new_bool_var(f"player_{candidate.player_id}") for candidate in candidates]
    position_requirements = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
    for position, required in position_requirements.items():
        model.add(sum(selected[index] for index, candidate in enumerate(candidates) if candidate.position == position) == required)
    for club in {candidate.club for candidate in candidates}:
        model.add(sum(selected[index] for index, candidate in enumerate(candidates) if candidate.club == club) <= 3)
    model.add(sum(selected[index] * candidate.cost for index, candidate in enumerate(candidates)) <= budget)
    model.maximize(sum(selected[index] * round(candidate.projected_points * 10_000) for index, candidate in enumerate(candidates)))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SquadOptimizationError("No legal squad can be built from the candidate pool within the budget")
    squad = [candidate for index, candidate in enumerate(candidates) if solver.value(selected[index])]
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
        projected_points=round(sum(candidate.projected_points for candidate in squad), 4),
        budget=budget,
        solver_status=solver.status_name(status),
        methodology=squad[0].methodology if squad else "unknown",
    )
