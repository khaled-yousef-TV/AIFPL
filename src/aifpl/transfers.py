from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field

from aifpl.current_projections import CurrentPlayerProjection
from aifpl.optimizer import SquadOptimizationError
from aifpl.rules import SquadPlayer, SquadRequest, validate_squad


class CurrentSquadState(BaseModel):
    player_ids: list[int] = Field(min_length=15, max_length=15)
    bank: int = Field(ge=0, description="Available funds in FPL tenths of a million")
    free_transfers: int = Field(ge=0, le=5)
    max_transfers: int = Field(default=2, ge=0, le=15)


@dataclass(frozen=True)
class TransferPlan:
    outgoing: list[CurrentPlayerProjection]
    incoming: list[CurrentPlayerProjection]
    resulting_squad: list[CurrentPlayerProjection]
    transfers_made: int
    hit_cost: int
    bank_after: int
    projected_points: float
    net_projected_points: float
    solver_status: str
    methodology: str


def plan_transfers(candidates: list[CurrentPlayerProjection], state: CurrentSquadState) -> TransferPlan:
    """Select the highest-net legal squad reachable from a current squad and bank."""
    candidate_by_id = {candidate.player_id: candidate for candidate in candidates}
    if len(candidate_by_id) != len(candidates):
        raise ValueError("Candidate player IDs must be unique")
    current_ids = set(state.player_ids)
    if len(current_ids) != 15:
        raise ValueError("Current squad player IDs must be unique")
    missing = current_ids - candidate_by_id.keys()
    if missing:
        raise ValueError(f"Current squad contains players absent from the candidate pool: {sorted(missing)}")
    _validate_current_squad([candidate_by_id[player_id] for player_id in current_ids], state.bank)
    model = cp_model.CpModel()
    selected = [model.new_bool_var(f"player_{candidate.player_id}") for candidate in candidates]
    for position, required in {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}.items():
        model.add(sum(selected[index] for index, candidate in enumerate(candidates) if candidate.position == position) == required)
    for club in {candidate.club for candidate in candidates}:
        model.add(sum(selected[index] for index, candidate in enumerate(candidates) if candidate.club == club) <= 3)
    incoming_indexes = [index for index, candidate in enumerate(candidates) if candidate.player_id not in current_ids]
    transfers_made = sum(selected[index] for index in incoming_indexes)
    model.add(transfers_made <= state.max_transfers)
    outgoing_value = sum(
        candidate.cost * (1 - selected[index])
        for index, candidate in enumerate(candidates)
        if candidate.player_id in current_ids
    )
    incoming_cost = sum(selected[index] * candidates[index].cost for index in incoming_indexes)
    model.add(incoming_cost <= state.bank + outgoing_value)
    projection_scale = 10_000
    excess_transfers = model.new_int_var(0, state.max_transfers, "excess_transfers")
    model.add(excess_transfers >= transfers_made - state.free_transfers)
    hit_cost_scaled = excess_transfers * 4 * projection_scale
    model.maximize(
        sum(selected[index] * round(candidate.projected_points * projection_scale) for index, candidate in enumerate(candidates))
        - hit_cost_scaled
        - transfers_made
    )
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SquadOptimizationError("No legal transfer plan is reachable from this squad and bank")
    resulting = [candidate for index, candidate in enumerate(candidates) if solver.value(selected[index])]
    resulting_ids = {candidate.player_id for candidate in resulting}
    outgoing = sorted((candidate_by_id[player_id] for player_id in current_ids - resulting_ids), key=lambda item: item.player_id)
    incoming = sorted((candidate_by_id[player_id] for player_id in resulting_ids - current_ids), key=lambda item: item.player_id)
    transfers = len(incoming)
    hit_cost = max(0, transfers - state.free_transfers) * 4
    bank_after = state.bank + sum(player.cost for player in outgoing) - sum(player.cost for player in incoming)
    _validate_current_squad(resulting, bank_after)
    return TransferPlan(
        outgoing=outgoing,
        incoming=incoming,
        resulting_squad=sorted(resulting, key=lambda item: (item.position, item.player_id)),
        transfers_made=transfers,
        hit_cost=hit_cost,
        bank_after=bank_after,
        projected_points=round(sum(player.projected_points for player in resulting), 4),
        net_projected_points=round(sum(player.projected_points for player in resulting) - hit_cost, 4),
        solver_status=solver.status_name(status),
        methodology=resulting[0].methodology,
    )


def _validate_current_squad(players: list[CurrentPlayerProjection], bank: int) -> None:
    validation = validate_squad(SquadRequest(players=[
        SquadPlayer(
            id=player.player_id, name=player.player_name, position=player.position, club=player.club,
            cost=player.cost, projected_points=player.projected_points,
        )
        for player in players
    ], budget=sum(player.cost for player in players) + bank))
    if not validation.legal:
        raise ValueError("Current/resulting squad is invalid: " + "; ".join(validation.errors))
