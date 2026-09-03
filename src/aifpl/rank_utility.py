from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from aifpl.game_state import GameState, rank_data_is_usable
from aifpl.strategy_policy import StrategyPolicy, derive_strategy_policy
from aifpl.template import PlayerTemplateState, ownership_source_confidence, target_cohort_eo

RANK_OBJECTIVE_SCALE = 0.25


@dataclass(frozen=True)
class RankUtilityBreakdown:
    expected_points: float
    strategic_upside: float
    template_fade_risk: float
    unnecessary_variance: float
    transfer_cost: float
    future_option_cost: float
    total: float
    mode: str

    def as_dict(self) -> dict[str, float | str]:
        return {
            "expected_points": round(self.expected_points, 4),
            "strategic_upside": round(self.strategic_upside, 4),
            "template_fade_risk": round(self.template_fade_risk, 4),
            "unnecessary_variance": round(self.unnecessary_variance, 4),
            "transfer_cost": round(self.transfer_cost, 4),
            "future_option_cost": round(self.future_option_cost, 4),
            "total": round(self.total, 4),
            "mode": self.mode,
        }


@dataclass(frozen=True)
class RankActionEvaluation:
    breakdown: RankUtilityBreakdown
    classification: str
    rationale: str


def player_rank_adjustment(
    player: object,
    state: GameState | None,
    template: PlayerTemplateState | None = None,
    *,
    captain: bool = False,
    triple_captain: bool = False,
    policy: StrategyPolicy | None = None,
) -> float:
    if (
        state is None
        or state.objective_mode != "RANK_MODE"
        or not rank_data_is_usable(state)
    ):
        return 0.0
    policy = policy or derive_strategy_policy(state, "RANK_MODE")
    projected = max(0.0, _number(player, "projected_points", 0.0))
    raw_ownership = _number_or_none(player, "selected_by_percent")
    effective_ownership = _number_or_none(player, "effective_ownership_pct")
    if effective_ownership is not None:
        field_eo, basis = effective_ownership, "effective_ownership"
    else:
        field_eo, basis = target_cohort_eo(
            0,
            template,
            raw_ownership,
            _number_or_none(player, "expected_captaincy"),
        )
    if field_eo is None:
        return 0.0
    reliability = ownership_source_confidence(basis)
    if reliability <= 0:
        return 0.0
    own_exposure = 300.0 if triple_captain else 200.0 if captain else 100.0
    exposure_gap = own_exposure - field_eo
    quality = min(1.0, projected / 8.0)
    strategic_upside = projected * max(0.0, exposure_gap) / 100.0 * quality * reliability
    template_fade_risk = projected * max(0.0, -exposure_gap) / 100.0 * quality * reliability
    template_protection = projected * min(max(0.0, field_eo), own_exposure) / 100.0 * quality * reliability
    variance = _variance_proxy(player, projected)
    if policy.status == "PROTECT_POSITION":
        value = template_protection - 0.25 * policy.template_fade_risk_weight * template_fade_risk
    elif policy.status == "BEHIND_TARGET":
        value = policy.strategic_upside_weight * strategic_upside - policy.template_fade_risk_weight * template_fade_risk
    else:
        value = (
            policy.strategic_upside_weight * strategic_upside
            + (1.0 - policy.template_fade_risk_weight) * template_protection
            - policy.template_fade_risk_weight * template_fade_risk
        )
    value -= policy.unnecessary_variance_weight * variance * (1.0 if exposure_gap > 0 else 0.5)
    return round(value, 4)


def rank_adjustment_coefficient(
    player: object,
    state: GameState | None,
    template: PlayerTemplateState | None = None,
    *,
    captain: bool = False,
    triple_captain: bool = False,
    week_weight: float = 1.0,
) -> int:
    return round(
        rank_objective_adjustment(
            player, state, template, captain=captain, triple_captain=triple_captain,
        )
        * week_weight * 10000
    )


def rank_objective_adjustment(
    player: object,
    state: GameState | None,
    template: PlayerTemplateState | None = None,
    *,
    captain: bool = False,
    triple_captain: bool = False,
    policy: StrategyPolicy | None = None,
) -> float:
    """Calibrate the game-theory signal before mixing it with projected points."""
    return round(
        player_rank_adjustment(
            player, state, template, captain=captain,
            triple_captain=triple_captain, policy=policy,
        ) * RANK_OBJECTIVE_SCALE,
        4,
    )


def evaluate_action(
    *,
    expected_points: float,
    state: GameState,
    strategic_upside: float = 0.0,
    template_fade_risk: float = 0.0,
    variance: float = 0.0,
    transfer_cost: float = 0.0,
    future_option_cost: float = 0.0,
    classification: str | None = None,
) -> RankActionEvaluation:
    policy = derive_strategy_policy(state)
    breakdown = RankUtilityBreakdown(
        expected_points=expected_points,
        strategic_upside=strategic_upside * policy.strategic_upside_weight,
        template_fade_risk=template_fade_risk * policy.template_fade_risk_weight,
        unnecessary_variance=variance * policy.unnecessary_variance_weight,
        transfer_cost=transfer_cost,
        future_option_cost=future_option_cost * policy.future_option_weight,
        total=0.0,
        mode=policy.objective_mode,
    )
    total = (
        breakdown.expected_points
        + breakdown.strategic_upside
        - breakdown.template_fade_risk
        - breakdown.unnecessary_variance
        - breakdown.transfer_cost
        - breakdown.future_option_cost
    )
    breakdown = RankUtilityBreakdown(**{**breakdown.__dict__, "total": round(total, 4)})
    selected_classification = classification or _classify(policy, strategic_upside, template_fade_risk, variance)
    return RankActionEvaluation(
        breakdown=breakdown,
        classification=selected_classification,
        rationale=_rationale(policy, selected_classification),
    )


def _classify(policy: StrategyPolicy, upside: float, fade: float, variance: float) -> str:
    if policy.status == "PROTECT_POSITION" and fade >= upside:
        return "UNNECESSARY_RISK"
    if policy.status == "BEHIND_TARGET" and upside > fade and variance > 0:
        return "CALCULATED_ATTACK"
    if policy.status == "BEHIND_TARGET":
        return "SELECTIVE_LEVERAGE"
    return "BALANCED_PLAY"


def _rationale(policy: StrategyPolicy, classification: str) -> str:
    if classification == "CALCULATED_ATTACK":
        return "The rank deficit justifies measured leverage after accounting for variance and future options."
    if classification == "UNNECESSARY_RISK":
        return "The move increases template-fade risk without enough strategic upside for the current rank state."
    return policy.rationale


def _variance_proxy(player: object, projected: float) -> float:
    lower = _number_or_none(player, "uncertainty_lower")
    upper = _number_or_none(player, "uncertainty_upper")
    if lower is None or upper is None:
        return projected * 0.15
    return max(0.0, upper - lower) / 2.0


def _number(value: object, name: str, default: float) -> float:
    number = _number_or_none(value, name)
    return default if number is None else number


def _number_or_none(value: object, name: str) -> float | None:
    try:
        raw = value.get(name) if isinstance(value, Mapping) else getattr(value, name)
        return None if raw is None else float(raw)
    except (AttributeError, TypeError, ValueError):
        return None
