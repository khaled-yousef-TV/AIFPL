from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Sequence

from aifpl.current_projections import CurrentPlayerProjection
from aifpl.live_calibration import UncertaintyProfile


@dataclass(frozen=True)
class PlayerDecisionMetrics:
    player_id: int
    projected_points: float
    expected_minutes: float
    start_probability: float
    minutes_basis: str
    availability_probability: float
    ownership_pct: float
    ownership_basis: str
    effective_ownership_pct: float | None
    differential_score: float
    uncertainty_lower: float | None
    uncertainty_upper: float | None
    confidence: str


@dataclass(frozen=True)
class CaptaincyAssessment:
    captain: PlayerDecisionMetrics
    vice_captain: PlayerDecisionMetrics
    safest_available: PlayerDecisionMetrics
    highest_upside_available: PlayerDecisionMetrics
    selection_basis: str = "highest_expected_points"
    safety_basis: str = "start_probability_then_expected_minutes"
    upside_basis: str = "expected_points_only"


@dataclass(frozen=True)
class PlanUncertainty:
    lower_points: float | None
    upper_points: float | None
    coverage: float | None
    observations: int
    confidence: str
    methodology: str
    aggregation: str


@dataclass(frozen=True)
class RobustnessAssessment:
    score: float
    minutes_security: float
    bench_strength: float
    bank_flexibility: float
    rotation_risk: float
    planned_transfer_dependency: float
    planned_transfers: int
    methodology: str = "deterministic_squad_robustness_v1"


@dataclass(frozen=True)
class PlanComparison:
    hold_net_points: float
    net_delta_vs_hold: float
    selected_reason: str
    confidence: str


@dataclass(frozen=True)
class TransferMoveExplanation:
    gameweek: int | None
    outgoing: PlayerDecisionMetrics | None
    incoming: PlayerDecisionMetrics | None
    projected_points_delta: float
    expected_minutes_delta: float
    ownership_delta_pct: float
    transfer_cost: float
    reasons: list[str]


def player_metrics(player: CurrentPlayerProjection, uncertainty: UncertaintyProfile | None = None) -> PlayerDecisionMetrics:
    expected_minutes = _expected_minutes(player)
    start_probability = _start_probability(player, expected_minutes)
    lower, upper, confidence = _interval(player, uncertainty)
    ownership = _bounded_number(_value(player, "selected_by_percent", 0.0), 0.0, 100.0)
    effective_ownership = _optional_number(_value(player, "effective_ownership_pct"))
    differential_ownership = effective_ownership if effective_ownership is not None else ownership
    methodology = str(_value(player, "methodology", "unknown"))
    return PlayerDecisionMetrics(
        player_id=int(_value(player, "player_id")),
        projected_points=round(float(_value(player, "projected_points", 0.0)), 4),
        expected_minutes=round(expected_minutes, 4),
        start_probability=round(start_probability, 4),
        minutes_basis="projection" if _value(player, "expected_minutes") is not None else "availability_assumption",
        availability_probability=round(_bounded_number(_value(player, "availability_multiplier", 1.0), 0.0, 1.0), 4),
        ownership_pct=round(ownership, 4),
        ownership_basis="effective_ownership" if effective_ownership is not None else "selected_by_percent",
        effective_ownership_pct=round(effective_ownership, 4) if effective_ownership is not None else None,
        # xPts already incorporates expected minutes where the projection model provides them.
        differential_score=round(float(_value(player, "projected_points", 0.0)) * (1 - min(100.0, max(0.0, differential_ownership)) / 100), 4),
        uncertainty_lower=lower,
        uncertainty_upper=upper,
        confidence=confidence,
    )


def select_vice(starters: Sequence[CurrentPlayerProjection], captain: CurrentPlayerProjection) -> CurrentPlayerProjection:
    alternatives = [player for player in starters if player.player_id != captain.player_id]
    if not alternatives:
        raise ValueError("A captain requires at least one eligible vice-captain")
    return max(alternatives, key=lambda player: (player.projected_points, _start_probability(player, _expected_minutes(player)), -player.player_id))


def captaincy_assessment(
    starters: Sequence[CurrentPlayerProjection], captain: CurrentPlayerProjection,
    vice_captain: CurrentPlayerProjection, uncertainty: UncertaintyProfile | None = None,
) -> CaptaincyAssessment:
    metrics = {player.player_id: player_metrics(player, uncertainty) for player in starters}
    safest = max(
        starters,
        key=lambda player: (
            _start_probability(player, _expected_minutes(player)),
            _expected_minutes(player),
            player.projected_points,
            -player.player_id,
        ),
    )
    highest_upside = max(
        starters,
        key=lambda player: (
            metrics[player.player_id].uncertainty_upper
            if metrics[player.player_id].uncertainty_upper is not None else player.projected_points,
            player.projected_points,
            -player.player_id,
        ),
    )
    return CaptaincyAssessment(
        captain=metrics[captain.player_id],
        vice_captain=metrics[vice_captain.player_id],
        safest_available=metrics[safest.player_id],
        highest_upside_available=metrics[highest_upside.player_id],
        upside_basis="calibrated_upper_interval" if uncertainty is not None and _uncertainty_value(uncertainty, "methodology") == _value(captain, "methodology", "unknown") else "expected_points_only",
    )


def plan_uncertainty(
    starters: Sequence[CurrentPlayerProjection], captain: CurrentPlayerProjection,
    methodology: str, uncertainty: UncertaintyProfile | None = None,
) -> PlanUncertainty:
    total = sum(player.projected_points for player in starters) + captain.projected_points
    if uncertainty is None or _uncertainty_value(uncertainty, "methodology") != methodology:
        return PlanUncertainty(None, None, None, 0, "uncalibrated", methodology, "not_available")
    appearances = len(starters) + 1
    return PlanUncertainty(
        lower_points=round(total + appearances * float(_uncertainty_value(uncertainty, "lower_residual", 0.0)), 4),
        upper_points=round(total + appearances * float(_uncertainty_value(uncertainty, "upper_residual", 0.0)), 4),
        coverage=_optional_number(_uncertainty_value(uncertainty, "coverage")),
        observations=int(_uncertainty_value(uncertainty, "observations", 0)),
        confidence=f"calibrated_{_uncertainty_value(uncertainty, 'confidence', 'unknown')}",
        methodology=methodology,
        aggregation="conservative_componentwise_residual_sum_v1",
    )


def horizon_uncertainty(
    lineups: Sequence[tuple[Sequence[CurrentPlayerProjection], CurrentPlayerProjection]], methodology: str,
    uncertainty: UncertaintyProfile | None = None,
) -> PlanUncertainty:
    total = sum(sum(player.projected_points for player in starters) + captain.projected_points for starters, captain in lineups)
    if uncertainty is None or _uncertainty_value(uncertainty, "methodology") != methodology:
        return PlanUncertainty(None, None, None, 0, "uncalibrated", methodology, "not_available")
    appearances = sum(len(starters) + 1 for starters, _ in lineups)
    return PlanUncertainty(
        lower_points=round(total + appearances * float(_uncertainty_value(uncertainty, "lower_residual", 0.0)), 4),
        upper_points=round(total + appearances * float(_uncertainty_value(uncertainty, "upper_residual", 0.0)), 4),
        coverage=_optional_number(_uncertainty_value(uncertainty, "coverage")),
        observations=int(_uncertainty_value(uncertainty, "observations", 0)),
        confidence=f"calibrated_{_uncertainty_value(uncertainty, 'confidence', 'unknown')}",
        methodology=methodology,
        aggregation="conservative_componentwise_residual_sum_v1",
    )


def robustness_assessment(
    squad: Sequence[CurrentPlayerProjection], starters: Sequence[CurrentPlayerProjection], bank: int,
    planned_transfers: int,
) -> RobustnessAssessment:
    starter_ids = {player.player_id for player in starters}
    bench = [player for player in squad if player.player_id not in starter_ids]
    minutes_security = _percent(
        sum(min(1.0, _expected_minutes(player) / 90) for player in starters) / max(1, len(starters))
    )
    starter_average = sum(player.projected_points for player in starters) / max(1, len(starters))
    bench_average = sum(player.projected_points for player in bench) / max(1, len(bench))
    bench_strength = _percent(bench_average / max(1.0, starter_average))
    # Two million in the bank is treated as fully flexible; the components remain available for other policies.
    bank_flexibility = _percent(bank / 20)
    planned_transfer_dependency = _percent(planned_transfers / 5)
    rotation_risk = round(100 - minutes_security, 4)
    score = max(0.0, min(
        100.0,
        0.55 * minutes_security + 0.25 * bench_strength + 0.20 * bank_flexibility - 0.10 * planned_transfer_dependency,
    ))
    return RobustnessAssessment(
        score=round(score, 4),
        minutes_security=round(minutes_security, 4),
        bench_strength=round(bench_strength, 4),
        bank_flexibility=round(bank_flexibility, 4),
        rotation_risk=rotation_risk,
        planned_transfer_dependency=round(planned_transfer_dependency, 4),
        planned_transfers=planned_transfers,
    )


def combine_robustness(assessments: Sequence[RobustnessAssessment]) -> RobustnessAssessment | None:
    if not assessments:
        return None
    count = len(assessments)
    return RobustnessAssessment(
        score=round(sum(item.score for item in assessments) / count, 4),
        minutes_security=round(sum(item.minutes_security for item in assessments) / count, 4),
        bench_strength=round(sum(item.bench_strength for item in assessments) / count, 4),
        bank_flexibility=round(sum(item.bank_flexibility for item in assessments) / count, 4),
        rotation_risk=round(sum(item.rotation_risk for item in assessments) / count, 4),
        planned_transfer_dependency=round(sum(item.planned_transfer_dependency for item in assessments) / count, 4),
        planned_transfers=sum(item.planned_transfers for item in assessments),
    )


def explain_transfers(
    gameweek: int | None, outgoing: Sequence[CurrentPlayerProjection], incoming: Sequence[CurrentPlayerProjection],
    hit_cost: int, uncertainty: UncertaintyProfile | None = None,
) -> list[TransferMoveExplanation]:
    remaining_outgoing = sorted(outgoing, key=lambda player: (player.position, player.player_id))
    explanations: list[TransferMoveExplanation] = []
    divisor = max(1, len(incoming), len(outgoing))
    for incoming_player in sorted(incoming, key=lambda player: (player.position, player.player_id)):
        match_index = next(
            (index for index, player in enumerate(remaining_outgoing) if player.position == incoming_player.position),
            0,
        )
        outgoing_player = remaining_outgoing.pop(match_index) if remaining_outgoing else None
        explanations.append(_move_explanation(gameweek, outgoing_player, incoming_player, hit_cost / divisor, uncertainty))
    for outgoing_player in remaining_outgoing:
        explanations.append(_move_explanation(gameweek, outgoing_player, None, hit_cost / divisor, uncertainty))
    return explanations


def _move_explanation(
    gameweek: int | None, outgoing: CurrentPlayerProjection | None, incoming: CurrentPlayerProjection | None,
    transfer_cost: float, uncertainty: UncertaintyProfile | None,
) -> TransferMoveExplanation:
    outgoing_metrics = player_metrics(outgoing, uncertainty) if outgoing is not None else None
    incoming_metrics = player_metrics(incoming, uncertainty) if incoming is not None else None
    projected_delta = (incoming.projected_points if incoming else 0.0) - (outgoing.projected_points if outgoing else 0.0)
    minutes_delta = (_expected_minutes(incoming) if incoming else 0.0) - (_expected_minutes(outgoing) if outgoing else 0.0)
    ownership_delta = (incoming.selected_by_percent if incoming else 0.0) - (outgoing.selected_by_percent if outgoing else 0.0)
    reasons: list[str] = []
    if projected_delta > 0:
        reasons.append("higher projected points")
    elif projected_delta < 0:
        reasons.append("lower projected points accepted for the wider plan")
    if minutes_delta > 0:
        reasons.append("higher expected minutes")
    elif minutes_delta < 0:
        reasons.append("lower expected minutes")
    if ownership_delta < 0:
        reasons.append("lower selected ownership")
    elif ownership_delta > 0:
        reasons.append("higher selected ownership")
    if transfer_cost:
        reasons.append("transfer hit included")
    if not reasons:
        reasons.append("squad constraint or future-plan balance")
    return TransferMoveExplanation(
        gameweek=gameweek,
        outgoing=outgoing_metrics,
        incoming=incoming_metrics,
        projected_points_delta=round(projected_delta, 4),
        expected_minutes_delta=round(minutes_delta, 4),
        ownership_delta_pct=round(ownership_delta, 4),
        transfer_cost=round(transfer_cost, 4),
        reasons=reasons,
    )


def _interval(player: CurrentPlayerProjection, uncertainty: UncertaintyProfile | None) -> tuple[float | None, float | None, str]:
    if uncertainty is None or _uncertainty_value(uncertainty, "methodology") != _value(player, "methodology", "unknown"):
        return None, None, "uncalibrated"
    projected = float(_value(player, "projected_points", 0.0))
    return (
        round(projected + float(_uncertainty_value(uncertainty, "lower_residual", 0.0)), 4),
        round(projected + float(_uncertainty_value(uncertainty, "upper_residual", 0.0)), 4),
        f"calibrated_{_uncertainty_value(uncertainty, 'confidence', 'unknown')}",
    )


def _expected_minutes(player: CurrentPlayerProjection | None) -> float:
    if player is None:
        return 0.0
    expected_minutes = _optional_number(_value(player, "expected_minutes"))
    if expected_minutes is not None:
        return max(0.0, expected_minutes)
    return max(0.0, 90 * _bounded_number(_value(player, "availability_multiplier", 1.0), 0.0, 1.0))


def _start_probability(player: CurrentPlayerProjection, expected_minutes: float) -> float:
    start_probability = _optional_number(_value(player, "start_probability"))
    if start_probability is not None:
        return max(0.0, min(1.0, start_probability))
    return max(0.0, min(1.0, expected_minutes / 90))


def _value(row: object, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _uncertainty_value(profile: object, name: str, default: Any = None) -> Any:
    if isinstance(profile, Mapping):
        return profile.get(name, default)
    return getattr(profile, name, default)


def _optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _bounded_number(value: Any, lower: float, upper: float) -> float:
    number = _optional_number(value)
    if number is None:
        return lower
    return max(lower, min(upper, number))


def _percent(value: float) -> float:
    return max(0.0, min(100.0, value * 100))
