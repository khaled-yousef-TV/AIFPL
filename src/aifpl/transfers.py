from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field

from aifpl.config import paid_transfer_safety_cap
from aifpl.current_projections import CurrentPlayerProjection
from aifpl.optimizer import SquadOptimizationError
from aifpl.rules import SquadPlayer, SquadRequest, club_key, validate_squad


class CurrentSquadState(BaseModel):
    player_ids: list[int] = Field(min_length=15, max_length=15)
    bank: int = Field(ge=0, description="Available funds in FPL tenths of a million")
    free_transfers: int = Field(ge=0, le=5)
    max_transfers: int | None = Field(
        default=None, ge=0, le=15,
        description="Legacy total-transfer cap; omitted means free transfers plus the paid safety cap",
    )
    paid_transfer_safety_cap: int | None = Field(
        default=None, ge=0, le=15,
        description="Optional paid-transfer cap; units are transfers, not points",
    )


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
    starting_xi: list[CurrentPlayerProjection]
    captain: CurrentPlayerProjection
    objective_projected_points: float
    net_objective_points: float


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
    paid_cap = (
        paid_transfer_safety_cap()
        if state.paid_transfer_safety_cap is None
        else state.paid_transfer_safety_cap
    )
    transfer_limit = min(15, state.free_transfers + paid_cap)
    if state.max_transfers is not None:
        transfer_limit = min(transfer_limit, state.max_transfers)
    model = cp_model.CpModel()
    selected = [model.new_bool_var(f"player_{candidate.player_id}") for candidate in candidates]
    starters = [model.new_bool_var(f"starter_{candidate.player_id}") for candidate in candidates]
    captains = [model.new_bool_var(f"captain_{candidate.player_id}") for candidate in candidates]
    for position, required in {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}.items():
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
    incoming_indexes = [index for index, candidate in enumerate(candidates) if candidate.player_id not in current_ids]
    transfers_made = sum(selected[index] for index in incoming_indexes)
    model.add(transfers_made <= transfer_limit)
    outgoing_value = sum(
        candidate.cost * (1 - selected[index])
        for index, candidate in enumerate(candidates)
        if candidate.player_id in current_ids
    )
    incoming_cost = sum(selected[index] * candidates[index].cost for index in incoming_indexes)
    model.add(incoming_cost <= state.bank + outgoing_value)
    projection_scale = 10_000
    excess_transfers = model.new_int_var(0, transfer_limit, "excess_transfers")
    model.add(excess_transfers >= transfers_made - state.free_transfers)
    hit_cost_scaled = excess_transfers * 4 * projection_scale
    model.maximize(
        sum(starters[index] * round(candidate.projected_points * projection_scale) for index, candidate in enumerate(candidates))
        + sum(captains[index] * round(candidate.projected_points * projection_scale) for index, candidate in enumerate(candidates))
        - hit_cost_scaled
        - transfers_made
    )
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SquadOptimizationError("No legal transfer plan is reachable from this squad and bank")
    resulting = [candidate for index, candidate in enumerate(candidates) if solver.value(selected[index])]
    starting_xi = [candidate for index, candidate in enumerate(candidates) if solver.value(starters[index])]
    captain = next(candidate for index, candidate in enumerate(candidates) if solver.value(captains[index]))
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
        projected_points=round(sum(player.projected_points for player in starting_xi) + captain.projected_points, 4),
        net_projected_points=round(sum(player.projected_points for player in starting_xi) + captain.projected_points - hit_cost, 4),
        solver_status=solver.status_name(status),
        methodology=resulting[0].methodology,
        starting_xi=sorted(starting_xi, key=lambda item: (("GK", "DEF", "MID", "FWD").index(item.position), item.player_id)),
        captain=captain,
        objective_projected_points=round(sum(player.projected_points for player in starting_xi) + captain.projected_points, 4),
        net_objective_points=round(sum(player.projected_points for player in starting_xi) + captain.projected_points - hit_cost, 4),
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
