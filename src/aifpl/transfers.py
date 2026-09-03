from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field

from aifpl.config import paid_transfer_safety_cap
from aifpl.current_projections import CurrentPlayerProjection
from aifpl.game_state import GameState, ObjectiveMode, rank_data_is_usable
from aifpl.optimizer import SquadOptimizationError
from aifpl.rank_utility import rank_adjustment_coefficient, rank_objective_adjustment
from aifpl.rules import SquadPlayer, SquadRequest, club_key, validate_squad
from aifpl.template import PlayerTemplateState, ownership_source_confidence, target_cohort_eo


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
class TransferImpact:
    out_id: int | None
    in_id: int
    projected_points_delta: float
    out_effective_ownership: float | None
    in_effective_ownership: float | None
    template_exposure_delta: float | None
    strategy_classification: str
    recommendation: str
    out_ownership_basis: str = "unavailable"
    in_ownership_basis: str = "unavailable"
    out_ownership_confidence: float | None = None
    in_ownership_confidence: float | None = None


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
    objective_mode: ObjectiveMode = "POINTS_MODE"
    objective_components: dict[str, float] | None = None
    strategy_classification: str = "POINTS_OPTIMAL"
    recommendation: str = "HOLD"
    transfer_impacts: list[TransferImpact] = field(default_factory=list)


def plan_transfers(
    candidates: list[CurrentPlayerProjection], state: CurrentSquadState,
    objective_mode: ObjectiveMode = "POINTS_MODE",
    game_state: GameState | None = None,
    template_states: Mapping[int, PlayerTemplateState] | None = None,
) -> TransferPlan:
    """Select the highest-net legal squad reachable from a current squad and bank."""
    candidate_by_id = {candidate.player_id: candidate for candidate in candidates}
    if objective_mode == "RANK_MODE" and (game_state is None or not game_state.rank_data_available):
        raise ValueError("RANK_MODE requires a GameState with an overall rank and target rank")
    if objective_mode == "RANK_MODE" and not rank_data_is_usable(game_state):
        raise ValueError("RANK_MODE requires rank data within the configured age limit")
    if objective_mode == "RANK_MODE" and game_state is not None and game_state.objective_mode != "RANK_MODE":
        game_state = game_state.model_copy(update={"objective_mode": "RANK_MODE"})
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
    objective = (
        sum(starters[index] * round(candidate.projected_points * projection_scale) for index, candidate in enumerate(candidates))
        + sum(captains[index] * round(candidate.projected_points * projection_scale) for index, candidate in enumerate(candidates))
        - hit_cost_scaled
        - transfers_made
    )
    if objective_mode == "RANK_MODE":
        objective += sum(
            selected[index]
            * rank_adjustment_coefficient(
                candidate, game_state, (template_states or {}).get(candidate.player_id),
            )
            for index, candidate in enumerate(candidates)
        )
        objective += sum(
            captains[index]
            * rank_adjustment_coefficient(
                candidate, game_state, (template_states or {}).get(candidate.player_id), captain=True,
            )
            for index, candidate in enumerate(candidates)
        )
    model.maximize(objective)
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
    projected_points = round(sum(player.projected_points for player in starting_xi) + captain.projected_points, 4)
    rank_adjustment = 0.0
    if objective_mode == "RANK_MODE":
        rank_adjustment = sum(
            rank_objective_adjustment(
                player, game_state, (template_states or {}).get(player.player_id),
            )
            for player in resulting
        )
        rank_adjustment += rank_objective_adjustment(
            captain, game_state, (template_states or {}).get(captain.player_id), captain=True,
        )
    objective_total = round(projected_points - hit_cost + rank_adjustment, 4)
    classification, recommendation = _classify_transfer(
        outgoing, incoming, game_state, template_states, transfers,
    )
    impacts = _transfer_impacts(
        outgoing, incoming, template_states, classification, recommendation,
    )
    return TransferPlan(
        outgoing=outgoing,
        incoming=incoming,
        resulting_squad=sorted(resulting, key=lambda item: (item.position, item.player_id)),
        transfers_made=transfers,
        hit_cost=hit_cost,
        bank_after=bank_after,
        projected_points=projected_points,
        net_projected_points=round(projected_points - hit_cost, 4),
        solver_status=solver.status_name(status),
        methodology=resulting[0].methodology,
        starting_xi=sorted(starting_xi, key=lambda item: (("GK", "DEF", "MID", "FWD").index(item.position), item.player_id)),
        captain=captain,
        objective_projected_points=projected_points,
        net_objective_points=objective_total,
        objective_mode=objective_mode,
        objective_components={
            "projected_points": projected_points,
            "hit_cost": -float(hit_cost),
            "rank_adjustment": round(rank_adjustment, 4),
            "total": objective_total,
        },
        strategy_classification=classification,
        recommendation=recommendation,
        transfer_impacts=impacts,
    )


def _classify_transfer(
    outgoing: list[CurrentPlayerProjection],
    incoming: list[CurrentPlayerProjection],
    game_state: GameState | None,
    template_states: Mapping[int, PlayerTemplateState] | None,
    transfers: int,
) -> tuple[str, str]:
    if game_state is None or not game_state.rank_data_available:
        return "POINTS_OPTIMAL", "MAKE_TRANSFER" if transfers else "HOLD"
    templates = template_states or {}
    leverage_delta = sum(
        rank_objective_adjustment(player, game_state, templates.get(player.player_id))
        for player in incoming
    ) - sum(
        rank_objective_adjustment(player, game_state, templates.get(player.player_id))
        for player in outgoing
    )
    if game_state.strategy_status == "PROTECT_POSITION" and leverage_delta < 0:
        return "UNNECESSARY_RISK", "HOLD"
    if game_state.strategy_status == "BEHIND_TARGET" and leverage_delta > 0:
        return "CALCULATED_ATTACK", "MAKE_TRANSFER"
    if game_state.strategy_status == "BEHIND_TARGET":
        return "SELECTIVE_LEVERAGE", "MAKE_TRANSFER" if transfers else "HOLD"
    if leverage_delta < 0:
        return "UNNECESSARY_RISK", "HOLD"
    return "BALANCED_PLAY", "MAKE_TRANSFER" if transfers else "HOLD"


def _transfer_impacts(
    outgoing: list[CurrentPlayerProjection],
    incoming: list[CurrentPlayerProjection],
    template_states: Mapping[int, PlayerTemplateState] | None,
    classification: str,
    recommendation: str,
) -> list[TransferImpact]:
    remaining_out = sorted(outgoing, key=lambda player: (player.position, player.player_id))
    impacts: list[TransferImpact] = []
    templates = template_states or {}
    for player in sorted(incoming, key=lambda item: (item.position, item.player_id)):
        match = next(
            (index for index, candidate in enumerate(remaining_out) if candidate.position == player.position),
            0,
        )
        outgoing_player = remaining_out.pop(match) if remaining_out else None
        out_eo, out_basis, out_confidence = _cohort_info(outgoing_player, templates)
        in_eo, in_basis, in_confidence = _cohort_info(player, templates)
        impacts.append(TransferImpact(
            out_id=outgoing_player.player_id if outgoing_player is not None else None,
            in_id=player.player_id,
            projected_points_delta=round(
                player.projected_points - (outgoing_player.projected_points if outgoing_player is not None else 0.0),
                4,
            ),
            out_effective_ownership=out_eo,
            in_effective_ownership=in_eo,
            out_ownership_basis=out_basis,
            in_ownership_basis=in_basis,
            out_ownership_confidence=out_confidence,
            in_ownership_confidence=in_confidence,
            template_exposure_delta=round(in_eo - out_eo, 4) if in_eo is not None and out_eo is not None else None,
            strategy_classification=classification,
            recommendation=recommendation,
        ))
    return impacts


def _cohort_eo(
    player: CurrentPlayerProjection | None,
    template_states: Mapping[int, PlayerTemplateState],
) -> float | None:
    return _cohort_info(player, template_states)[0]


def _cohort_info(
    player: CurrentPlayerProjection | None,
    template_states: Mapping[int, PlayerTemplateState],
) -> tuple[float | None, str, float | None]:
    if player is None:
        return None, "unavailable", None
    if player.effective_ownership_pct is not None:
        basis = "effective_ownership"
        return player.effective_ownership_pct, basis, ownership_source_confidence(basis)
    value, basis = target_cohort_eo(
        player.player_id, template_states.get(player.player_id), player.selected_by_percent,
        player.expected_captaincy,
    )
    return value, basis, ownership_source_confidence(basis) if value is not None else None


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
