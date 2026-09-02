from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from aifpl.current_projections import CurrentPlayerProjection
from aifpl.game_state import GameState
from aifpl.rank_utility import rank_objective_adjustment
from aifpl.strategy_policy import derive_strategy_policy
from aifpl.template import PlayerTemplateState, target_cohort_eo


@dataclass(frozen=True)
class CaptaincyOption:
    player_id: int
    projected_points: float
    target_cohort_eo: float | None
    own_exposure_if_captained: float
    net_exposure: float | None
    score: float
    classification: str


@dataclass(frozen=True)
class CaptaincyChoice:
    captain: CurrentPlayerProjection
    vice_captain: CurrentPlayerProjection
    options: list[CaptaincyOption]
    mode: str


def choose_captain(
    starters: Sequence[CurrentPlayerProjection],
    state: GameState | None = None,
    template_states: Mapping[int, PlayerTemplateState] | None = None,
    triple_captain: bool = False,
) -> CaptaincyChoice:
    if len(starters) < 2:
        raise ValueError("Captaincy requires at least two starters")
    if state is None or state.objective_mode != "RANK_MODE" or not state.rank_data_available:
        ordered = sorted(starters, key=lambda player: (-player.projected_points, player.player_id))
        return CaptaincyChoice(ordered[0], ordered[1], [
            CaptaincyOption(player.player_id, player.projected_points, None, 300.0 if triple_captain else 200.0, None, player.projected_points, "points")
            for player in ordered
        ], "POINTS_MODE")
    policy = derive_strategy_policy(state, "RANK_MODE")
    template_states = template_states or {}
    options: list[CaptaincyOption] = []
    for player in starters:
        field_eo, _ = target_cohort_eo(player.player_id, template_states.get(player.player_id), player.selected_by_percent)
        own_exposure = 300.0 if triple_captain else 200.0
        net = round(own_exposure - field_eo, 4) if field_eo is not None else None
        score = player.projected_points + rank_objective_adjustment(
            player, state, template_states.get(player.player_id), captain=True,
            triple_captain=triple_captain, policy=policy,
        )
        classification = _classification(policy.status, field_eo, net)
        options.append(CaptaincyOption(
            player_id=player.player_id,
            projected_points=player.projected_points,
            target_cohort_eo=field_eo,
            own_exposure_if_captained=own_exposure,
            net_exposure=net,
            score=round(score, 4),
            classification=classification,
        ))
    options.sort(key=lambda option: (-option.score, -option.projected_points, option.player_id))
    by_id = {player.player_id: player for player in starters}
    captain = by_id[options[0].player_id]
    vice = max(
        (player for player in starters if player.player_id != captain.player_id),
        key=lambda player: next(option.score for option in options if option.player_id == player.player_id),
    )
    return CaptaincyChoice(captain, vice, options, "RANK_MODE")


def _classification(status: str, field_eo: float | None, net: float | None) -> str:
    if field_eo is None or net is None:
        return "BALANCED_LEVERAGE"
    if status == "PROTECT_POSITION" and field_eo >= 100:
        return "SHIELD"
    if status == "BEHIND_TARGET" and field_eo < 70:
        return "AGGRESSIVE_LEVERAGE"
    return "BALANCED_LEVERAGE"
