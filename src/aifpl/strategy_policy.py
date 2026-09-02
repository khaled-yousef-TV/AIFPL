from __future__ import annotations

from pydantic import BaseModel, Field

from aifpl.game_state import GameState, ObjectiveMode, StrategyStatus


class StrategyPolicy(BaseModel):
    objective_mode: ObjectiveMode
    status: StrategyStatus
    risk_level: float = Field(ge=0, le=1)
    strategic_upside_weight: float = Field(ge=0)
    template_fade_risk_weight: float = Field(ge=0)
    unnecessary_variance_weight: float = Field(ge=0)
    future_option_weight: float = Field(ge=0)
    rank_gap_ratio: float | None = Field(default=None, ge=0)
    urgency: float = Field(default=0.0, ge=0, le=1)
    chip_advantage: float = Field(default=0.0, ge=0, le=1)
    rationale: str


def derive_strategy_policy(
    state: GameState,
    requested_mode: ObjectiveMode | None = None,
) -> StrategyPolicy:
    mode = requested_mode or state.objective_mode
    if mode == "RANK_MODE" and not state.rank_data_available:
        raise ValueError("RANK_MODE requires an overall rank and target rank")
    status = state.strategy_status
    gap_ratio = state.rank_gap_ratio
    urgency = _urgency(state.gameweeks_remaining)
    chip_advantage = _chip_advantage(state.chips_remaining)
    if mode == "POINTS_MODE":
        return StrategyPolicy(
            objective_mode=mode,
            status=status,
            risk_level=state.risk_level,
            strategic_upside_weight=0.0,
            template_fade_risk_weight=0.0,
            unnecessary_variance_weight=0.0,
            future_option_weight=0.0,
            rank_gap_ratio=gap_ratio,
            urgency=0.0,
            chip_advantage=0.0,
            rationale="POINTS_MODE preserves the expected-points objective.",
        )
    if status == "PROTECT_POSITION":
        return StrategyPolicy(
            objective_mode=mode,
            status=status,
            risk_level=min(state.risk_level, 0.35),
            strategic_upside_weight=0.15 + 0.10 * urgency,
            template_fade_risk_weight=0.90 + 0.10 * (1 - urgency),
            unnecessary_variance_weight=0.70 + 0.20 * (1 - urgency),
            future_option_weight=0.60 + 0.20 * chip_advantage,
            rank_gap_ratio=gap_ratio,
            urgency=urgency,
            chip_advantage=chip_advantage,
            rationale="Protect the achieved rank by reducing template-fade and unnecessary variance.",
        )
    if status == "BEHIND_TARGET":
        gap_need = _clamp(((gap_ratio or 1.0) - 1.0) / 4.0)
        return StrategyPolicy(
            objective_mode=mode,
            status=status,
            risk_level=max(state.risk_level, 0.65),
            strategic_upside_weight=0.65 + 0.25 * gap_need + 0.10 * urgency,
            template_fade_risk_weight=0.35 - 0.15 * gap_need,
            unnecessary_variance_weight=0.35 - 0.10 * gap_need,
            future_option_weight=0.25 + 0.20 * chip_advantage,
            rank_gap_ratio=gap_ratio,
            urgency=urgency,
            chip_advantage=chip_advantage,
            rationale="Pursue calculated leverage because the current rank is behind target.",
        )
    return StrategyPolicy(
        objective_mode=mode,
        status=status,
        risk_level=state.risk_level,
        strategic_upside_weight=0.45 + 0.15 * urgency,
        template_fade_risk_weight=0.55 + 0.10 * (1 - urgency),
        unnecessary_variance_weight=0.40 + 0.10 * (1 - urgency),
        future_option_weight=0.40 + 0.20 * chip_advantage,
        rank_gap_ratio=gap_ratio,
        urgency=urgency,
        chip_advantage=chip_advantage,
        rationale="Balance template protection and selective leverage while on track.",
    )


def _urgency(gameweeks_remaining: int | None) -> float:
    if gameweeks_remaining is None:
        return 0.0
    return _clamp(1.0 - gameweeks_remaining / 38.0)


def _chip_advantage(chips_remaining: dict[str, int]) -> float:
    return _clamp(sum(max(0, int(value)) for value in chips_remaining.values()) / 8.0)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
