from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import isclose
from typing import Any, Mapping

from aifpl.config import (
    bank_shortfall_penalty,
    bench_min_projection,
    bench_weight,
    dead_bench_penalty,
    horizon_forecast_distance_decay,
    horizon_min_confidence_weight,
    minimum_bank_tenths,
    paid_transfer_safety_cap,
    transfer_penalty,
)
from aifpl.rules import DEFAULT_BUDGET_TENTHS, SquadPlayer, SquadRequest, legal_formations, validate_squad
from aifpl.game_state import GameState, ObjectiveMode
from aifpl.rank_utility import rank_adjustment_coefficient, rank_objective_adjustment
from aifpl.template import PlayerTemplateState


OBJECTIVE_SCALE = 10_000
PREFERRED_PLAYER_BONUS = 100 / OBJECTIVE_SCALE
DEAD_BENCH_ALLOWANCE = 2


@dataclass(frozen=True)
class HorizonObjectiveSettings:
    """All values used by the multi-week objective, in projected-point units."""

    strategy_hit_penalty: float
    churn_penalty: float
    bench_weight: float
    bank_shortfall_penalty: float
    dead_bench_penalty: float
    minimum_bank_tenths: int
    minimum_confidence_weight: float
    forecast_distance_decay: float
    objective_mode: ObjectiveMode = "POINTS_MODE"
    game_state: GameState | None = None
    template_states: Mapping[int, PlayerTemplateState] | None = None


@dataclass(frozen=True)
class HorizonWeekWeight:
    gameweek: int
    odds_coverage: float
    information_weight: float
    forecast_distance_weight: float
    week_weight: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "gameweek": self.gameweek,
            "odds_coverage": round(self.odds_coverage, 6),
            "information_weight": round(self.information_weight, 6),
            "forecast_distance_weight": round(self.forecast_distance_weight, 6),
            "week_weight": round(self.week_weight, 6),
        }


@dataclass(frozen=True)
class HorizonObjectiveBreakdown:
    starter_points: float
    captain_points: float
    bench_points: float
    strategy_hit_penalty: float
    churn_penalty: float
    bank_shortfall_penalty: float
    dead_bench_penalty: float
    preferred_bonus: float
    differential_bonus: float
    odds_coverage: float
    information_weight: float
    forecast_distance_weight: float
    week_weight: float
    total: float
    rank_adjustment: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "starter_points": round(self.starter_points, 4),
            "captain_points": round(self.captain_points, 4),
            "bench_points": round(self.bench_points, 4),
            "strategy_hit_penalty": round(self.strategy_hit_penalty, 4),
            "churn_penalty": round(self.churn_penalty, 4),
            "bank_shortfall_penalty": round(self.bank_shortfall_penalty, 4),
            "dead_bench_penalty": round(self.dead_bench_penalty, 4),
            "preferred_bonus": round(self.preferred_bonus, 4),
            "differential_bonus": round(self.differential_bonus, 4),
            "odds_coverage": round(self.odds_coverage, 6),
            "information_weight": round(self.information_weight, 6),
            "forecast_distance_weight": round(self.forecast_distance_weight, 6),
            "week_weight": round(self.week_weight, 6),
            "rank_adjustment": round(self.rank_adjustment, 4),
            "total": round(self.total, 4),
        }


class HorizonPlanValidationError(ValueError):
    """Raised when a returned horizon plan fails deterministic audit checks."""


def horizon_objective_settings(
    strategy_hit_penalty: float,
    churn_penalty_override: float | None = None,
    objective_mode: ObjectiveMode = "POINTS_MODE",
    game_state: GameState | None = None,
    template_states: Mapping[int, PlayerTemplateState] | None = None,
) -> HorizonObjectiveSettings:
    if objective_mode == "RANK_MODE" and (game_state is None or not game_state.rank_data_available):
        raise ValueError("RANK_MODE requires a GameState with an overall rank and target rank")
    if objective_mode == "RANK_MODE" and game_state is not None and game_state.objective_mode != "RANK_MODE":
        game_state = game_state.model_copy(update={"objective_mode": "RANK_MODE"})
    return HorizonObjectiveSettings(
        strategy_hit_penalty=strategy_hit_penalty,
        churn_penalty=transfer_penalty() if churn_penalty_override is None else churn_penalty_override,
        bench_weight=bench_weight(),
        bank_shortfall_penalty=bank_shortfall_penalty(),
        dead_bench_penalty=dead_bench_penalty(),
        minimum_bank_tenths=minimum_bank_tenths(),
        minimum_confidence_weight=horizon_min_confidence_weight(),
        forecast_distance_decay=horizon_forecast_distance_decay(),
        objective_mode=objective_mode,
        game_state=game_state,
        template_states=template_states,
    )


def horizon_week_weights(
    rows: list[Any], gameweeks: list[int], settings: HorizonObjectiveSettings,
) -> list[HorizonWeekWeight]:
    weights: list[HorizonWeekWeight] = []
    for index, gameweek in enumerate(gameweeks):
        week_rows = [row for row in rows if row.gameweek == gameweek]
        fixture_total = sum(max(0, row.fixture_count) for row in week_rows)
        odds_total = sum(
            min(max(0, row.fixture_count), max(0, row.odds_backed_fixture_count))
            for row in week_rows
        )
        coverage = min(1.0, odds_total / fixture_total) if fixture_total else 0.0
        information = settings.minimum_confidence_weight + (
            1 - settings.minimum_confidence_weight
        ) * coverage
        distance = settings.forecast_distance_decay ** index
        weights.append(HorizonWeekWeight(
            gameweek=gameweek,
            odds_coverage=coverage,
            information_weight=information,
            forecast_distance_weight=distance,
            week_weight=information * distance,
        ))
    return weights


def objective_scaled(value: float) -> int:
    """Quantize a point term exactly as it is represented in CP-SAT."""
    return round(value * OBJECTIVE_SCALE)


def objective_float(scaled_value: int) -> float:
    return scaled_value / OBJECTIVE_SCALE


def starter_coefficient(projected_points: float, week_weight: float) -> int:
    return objective_scaled(projected_points * week_weight)


def captain_coefficient(projected_points: float, week_weight: float) -> int:
    return objective_scaled(projected_points * week_weight)


def bench_coefficient(projected_points: float, week_weight: float, weight: float) -> int:
    return objective_scaled(projected_points * weight * week_weight)


def strategy_hit_coefficient(
    week_weight: float, settings: HorizonObjectiveSettings, unlimited_transfers: bool,
) -> int:
    return 0 if unlimited_transfers else objective_scaled(settings.strategy_hit_penalty * week_weight)


def churn_coefficient(week_weight: float, settings: HorizonObjectiveSettings) -> int:
    return objective_scaled(settings.churn_penalty * week_weight)


def bank_shortfall_coefficient(week_weight: float, settings: HorizonObjectiveSettings) -> int:
    return objective_scaled(settings.bank_shortfall_penalty * week_weight)


def dead_bench_coefficient(week_weight: float, settings: HorizonObjectiveSettings) -> int:
    return objective_scaled(settings.dead_bench_penalty * week_weight)


def preferred_coefficient(week_weight: float) -> int:
    return objective_scaled(PREFERRED_PLAYER_BONUS * week_weight)


def differential_coefficient(
    projected_points: float, selected_by_percent: float, appetite: float, week_weight: float,
) -> int:
    return objective_scaled(
        projected_points * appetite * (100 - selected_by_percent) / 100 * week_weight
    )


def horizon_objective_breakdown(
    *,
    rows_by_id: Mapping[int, Any],
    selected_ids: set[int],
    starter_ids: set[int],
    captain_id: int,
    transfers_made: int,
    free_transfers_before: int,
    bank_after: int,
    bench_floor: float,
    preferred_player_ids: set[int] | None,
    differential_appetite: float,
    week: HorizonWeekWeight,
    settings: HorizonObjectiveSettings,
    unlimited_transfers: bool,
) -> HorizonObjectiveBreakdown:
    selected_rows = [rows_by_id[player_id] for player_id in sorted(selected_ids)]
    starter_rows = [rows_by_id[player_id] for player_id in sorted(starter_ids)]
    bench_rows = [row for row in selected_rows if row.player_id not in starter_ids]
    dead_count = sum(1 for row in bench_rows if row.projected_points < bench_floor)
    dead_excess = max(0, dead_count - DEAD_BENCH_ALLOWANCE)
    excess_transfers = max(0, transfers_made - free_transfers_before)

    starter_points = sum(
        objective_float(starter_coefficient(row.projected_points, week.week_weight))
        for row in starter_rows
    )
    captain_points = objective_float(
        captain_coefficient(rows_by_id[captain_id].projected_points, week.week_weight)
    )
    bench_points = sum(
        objective_float(bench_coefficient(row.projected_points, week.week_weight, settings.bench_weight))
        for row in bench_rows
    )
    strategy_penalty = -objective_float(
        excess_transfers * strategy_hit_coefficient(week.week_weight, settings, unlimited_transfers)
    )
    churn = -objective_float(transfers_made * churn_coefficient(week.week_weight, settings))
    shortfall = max(0, settings.minimum_bank_tenths - bank_after)
    shortfall_penalty = -objective_float(
        shortfall * bank_shortfall_coefficient(week.week_weight, settings)
    )
    dead_penalty = -objective_float(
        dead_excess * dead_bench_coefficient(week.week_weight, settings)
    )
    preferred = sum(
        preferred_coefficient(week.week_weight) for player_id in selected_ids
        if player_id in (preferred_player_ids or set())
    ) / OBJECTIVE_SCALE
    differential = sum(
        differential_coefficient(
            row.projected_points, row.selected_by_percent, differential_appetite, week.week_weight,
        )
        for row in selected_rows
    ) / OBJECTIVE_SCALE
    rank_adjustment = 0.0
    if settings.objective_mode == "RANK_MODE":
        rank_adjustment = sum(
            objective_float(rank_adjustment_coefficient(
                row, settings.game_state, (settings.template_states or {}).get(row.player_id),
                week_weight=week.week_weight,
            ))
            for row in selected_rows
        )
        rank_adjustment += objective_float(rank_adjustment_coefficient(
            rows_by_id[captain_id], settings.game_state,
            (settings.template_states or {}).get(captain_id), captain=True,
            week_weight=week.week_weight,
        ))
    total = (
        starter_points + captain_points + bench_points + strategy_penalty + churn
        + shortfall_penalty + dead_penalty + preferred
        + (rank_adjustment if settings.objective_mode == "RANK_MODE" else differential)
    )
    return HorizonObjectiveBreakdown(
        starter_points=starter_points,
        captain_points=captain_points,
        bench_points=bench_points,
        strategy_hit_penalty=strategy_penalty,
        churn_penalty=churn,
        bank_shortfall_penalty=shortfall_penalty,
        dead_bench_penalty=dead_penalty,
        preferred_bonus=preferred,
        differential_bonus=differential,
        odds_coverage=week.odds_coverage,
        information_weight=week.information_weight,
        forecast_distance_weight=week.forecast_distance_weight,
        week_weight=week.week_weight,
        total=total,
        rank_adjustment=rank_adjustment,
    )


def best_objective_lineup(
    squad: list[Any],
    rows_by_id: Mapping[int, Any],
    week: HorizonWeekWeight,
    settings: HorizonObjectiveSettings,
    bench_floor: float,
) -> tuple[list[int], int]:
    """Select a deterministic lineup using the same quantized lineup terms."""
    by_position = {
        position: [player.player_id for player in squad if player.position == position]
        for position in ("GK", "DEF", "MID", "FWD")
    }
    best: tuple[int, tuple[int, ...], int] | None = None
    for defenders, midfielders, forwards in legal_formations():
        for goalkeeper in combinations(by_position["GK"], 1):
            for defender_group in combinations(by_position["DEF"], defenders):
                for midfielder_group in combinations(by_position["MID"], midfielders):
                    for forward_group in combinations(by_position["FWD"], forwards):
                        starter_ids = set(goalkeeper + defender_group + midfielder_group + forward_group)
                        if settings.objective_mode == "RANK_MODE":
                            from aifpl.captaincy_strategy import choose_captain

                            starter_players = [
                                player for player in squad if player.player_id in starter_ids
                            ]
                            captain_id = choose_captain(
                                starter_players, settings.game_state, settings.template_states,
                            ).captain.player_id
                        else:
                            ranked = sorted(
                                starter_ids,
                                key=lambda player_id: (-rows_by_id[player_id].projected_points, player_id),
                            )
                            captain_id = ranked[0]
                        bench_ids = set(rows_by_id) & ({player.player_id for player in squad} - starter_ids)
                        dead_excess = max(
                            0,
                            sum(rows_by_id[player_id].projected_points < bench_floor for player_id in bench_ids)
                            - DEAD_BENCH_ALLOWANCE,
                        )
                        score = (
                            sum(starter_coefficient(rows_by_id[player_id].projected_points, week.week_weight) for player_id in starter_ids)
                            + captain_coefficient(rows_by_id[captain_id].projected_points, week.week_weight)
                            + sum(
                                bench_coefficient(rows_by_id[player_id].projected_points, week.week_weight, settings.bench_weight)
                                for player_id in bench_ids
                            )
                            - dead_excess * dead_bench_coefficient(week.week_weight, settings)
                        )
                        if settings.objective_mode == "RANK_MODE":
                            score += sum(
                                rank_adjustment_coefficient(
                                    rows_by_id[player_id], settings.game_state,
                                    (settings.template_states or {}).get(player_id),
                                    week_weight=week.week_weight,
                                )
                                for player_id in {player.player_id for player in squad}
                            )
                            score += rank_adjustment_coefficient(
                                rows_by_id[captain_id], settings.game_state,
                                (settings.template_states or {}).get(captain_id),
                                captain=True, week_weight=week.week_weight,
                            )
                        key = tuple(sorted(starter_ids))
                        candidate = (score, key, captain_id)
                        if best is None or score > best[0] or (score == best[0] and key < best[1]):
                            best = candidate
    if best is None:
        raise ValueError("Cannot select a legal objective lineup")
    return list(best[1]), best[2]


def aggregate_objective_components(
    components: list[Mapping[str, float]],
) -> dict[str, float]:
    keys = sorted({key for component in components for key in component})
    return {
        key: round(sum(float(component.get(key, 0.0)) for component in components), 4)
        for key in keys
    }


def _audit_week_weight(
    rows: list[Any], gameweek: int, index: int, settings: HorizonObjectiveSettings,
) -> HorizonWeekWeight:
    week_rows = [row for row in rows if row.gameweek == gameweek]
    fixture_total = sum(max(0, row.fixture_count) for row in week_rows)
    odds_total = sum(
        min(max(0, row.fixture_count), max(0, row.odds_backed_fixture_count))
        for row in week_rows
    )
    coverage = min(1.0, odds_total / fixture_total) if fixture_total else 0.0
    information = settings.minimum_confidence_weight + (
        1 - settings.minimum_confidence_weight
    ) * coverage
    distance = settings.forecast_distance_decay ** index
    return HorizonWeekWeight(
        gameweek=gameweek,
        odds_coverage=coverage,
        information_weight=information,
        forecast_distance_weight=distance,
        week_weight=information * distance,
    )


def _audit_objective_breakdown(
    *,
    rows_by_id: Mapping[int, Any],
    selected_ids: set[int],
    starter_ids: set[int],
    captain_id: int,
    transfers_made: int,
    free_transfers_before: int,
    bank_after: int,
    bench_floor: float,
    preferred_player_ids: set[int],
    differential_appetite: float,
    week: HorizonWeekWeight,
    settings: HorizonObjectiveSettings,
    unlimited_transfers: bool,
) -> dict[str, float]:
    """Recompute objective values without reading any solver variables.

    This intentionally mirrors the public accounting contract rather than
    calling the CP-SAT expression builder, so a returned plan is audited from
    its serialized player and transition data.
    """
    selected_rows = [rows_by_id[player_id] for player_id in sorted(selected_ids)]
    starter_rows = [rows_by_id[player_id] for player_id in sorted(starter_ids)]
    bench_rows = [row for row in selected_rows if row.player_id not in starter_ids]
    dead_excess = max(
        0,
        sum(row.projected_points < bench_floor for row in bench_rows) - DEAD_BENCH_ALLOWANCE,
    )
    excess_transfers = max(0, transfers_made - free_transfers_before)
    q = lambda value: round(value * OBJECTIVE_SCALE) / OBJECTIVE_SCALE
    starter_points = sum(q(row.projected_points * week.week_weight) for row in starter_rows)
    captain_points = q(rows_by_id[captain_id].projected_points * week.week_weight)
    bench_points = sum(
        q(row.projected_points * settings.bench_weight * week.week_weight)
        for row in bench_rows
    )
    strategy_hit_penalty = -excess_transfers * q(
        0 if unlimited_transfers else settings.strategy_hit_penalty * week.week_weight
    )
    churn_penalty_value = -transfers_made * q(settings.churn_penalty * week.week_weight)
    shortfall = max(0, settings.minimum_bank_tenths - bank_after)
    bank_shortfall_value = -shortfall * q(settings.bank_shortfall_penalty * week.week_weight)
    dead_bench_value = -dead_excess * q(settings.dead_bench_penalty * week.week_weight)
    preferred_bonus = sum(
        q(PREFERRED_PLAYER_BONUS * week.week_weight)
        for player_id in selected_ids if player_id in preferred_player_ids
    )
    differential_bonus = sum(
        q(row.projected_points * differential_appetite * (100 - row.selected_by_percent) / 100 * week.week_weight)
        for row in selected_rows
    )
    rank_adjustment = 0.0
    if settings.objective_mode == "RANK_MODE":
        rank_adjustment = sum(
            q(rank_objective_adjustment(
                row, settings.game_state, (settings.template_states or {}).get(row.player_id),
            ) * week.week_weight)
            for row in selected_rows
        )
        rank_adjustment += q(
            rank_objective_adjustment(
                rows_by_id[captain_id], settings.game_state,
                (settings.template_states or {}).get(captain_id), captain=True,
            ) * week.week_weight
        )
    total = (
        starter_points + captain_points + bench_points + strategy_hit_penalty
        + churn_penalty_value + bank_shortfall_value + dead_bench_value
        + preferred_bonus
        + (rank_adjustment if settings.objective_mode == "RANK_MODE" else differential_bonus)
    )
    return {
        "starter_points": round(starter_points, 4),
        "captain_points": round(captain_points, 4),
        "bench_points": round(bench_points, 4),
        "strategy_hit_penalty": round(strategy_hit_penalty, 4),
        "churn_penalty": round(churn_penalty_value, 4),
        "bank_shortfall_penalty": round(bank_shortfall_value, 4),
        "dead_bench_penalty": round(dead_bench_value, 4),
        "preferred_bonus": round(preferred_bonus, 4),
        "differential_bonus": round(differential_bonus, 4),
        "rank_adjustment": round(rank_adjustment, 4),
        "odds_coverage": round(week.odds_coverage, 6),
        "information_weight": round(week.information_weight, 6),
        "forecast_distance_weight": round(week.forecast_distance_weight, 6),
        "week_weight": round(week.week_weight, 6),
        "total": round(total, 4),
    }


def _assert_close(actual: float, expected: float, label: str) -> None:
    if not isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=0.00011):
        raise HorizonPlanValidationError(f"{label}: expected {expected}, received {actual}")


def validate_horizon_plan(
    plan: Any,
    rows: list[Any],
    state: Any,
    *,
    pre_season: bool = False,
    decision_hit_penalty: float = 4.0,
    churn_penalty: float | None = None,
    preferred_player_ids: set[int] | None = None,
    differential_appetite: float = 0.0,
    bench_floor: float | None = None,
    paid_transfer_cap: int | None = None,
    settings: HorizonObjectiveSettings | None = None,
    initial_purchase_prices: Mapping[int, int] | None = None,
) -> None:
    """Audit a complete horizon plan with deterministic, non-solver checks."""
    settings = settings or horizon_objective_settings(decision_hit_penalty, churn_penalty)
    if getattr(plan, "objective_mode", "POINTS_MODE") != settings.objective_mode:
        raise HorizonPlanValidationError("Plan objective mode does not match its accounting settings")
    paid_cap = paid_transfer_safety_cap() if paid_transfer_cap is None else paid_transfer_cap
    gameweeks = sorted({row.gameweek for row in rows})
    if [week.gameweek for week in plan.gameweeks] != gameweeks:
        raise HorizonPlanValidationError("Returned plan gameweeks do not match the projection horizon")
    if not 0 <= paid_cap <= 15:
        raise HorizonPlanValidationError("Paid-transfer safety cap is outside 0..15")

    current_ids = set(state.player_ids)
    if len(current_ids) not in (0, 15):
        raise HorizonPlanValidationError("Initial squad must contain zero or fifteen unique players")
    if initial_purchase_prices is None:
        first_rows = {
            row.player_id: row
            for row in rows if row.gameweek == gameweeks[0]
        }
        configured_prices = getattr(state, "purchase_prices", None)
        purchase_book = dict(
            configured_prices
            if configured_prices is not None
            else {player_id: first_rows[player_id].cost for player_id in current_ids}
        )
    else:
        purchase_book = dict(initial_purchase_prices)
    if current_ids:
        if set(purchase_book) != current_ids:
            raise HorizonPlanValidationError("Initial purchase-price book does not match the squad")
    else:
        purchase_book = {}
    bank = state.bank
    free = 5 if pre_season else state.free_transfers
    if not 1 <= free <= 5:
        raise HorizonPlanValidationError("Free transfers before the horizon must be within 1..5")
    previous_ids = current_ids
    objective_components: list[Mapping[str, float]] = []
    total_projected = 0.0
    total_hits = 0
    total_net = 0.0
    audit_bench_floor = bench_min_projection() if bench_floor is None else bench_floor

    for index, week in enumerate(plan.gameweeks):
        gameweek = gameweeks[index]
        week_rows = {
            row.player_id: row for row in rows if row.gameweek == gameweek
        }
        selected_ids = [player.player_id for player in week.resulting_squad]
        incoming_ids = [player.player_id for player in week.incoming]
        outgoing_ids = [player.player_id for player in week.outgoing]
        if len(selected_ids) != len(set(selected_ids)):
            raise HorizonPlanValidationError(f"GW{gameweek} resulting squad contains duplicate players")
        if len(incoming_ids) != len(set(incoming_ids)) or len(outgoing_ids) != len(set(outgoing_ids)):
            raise HorizonPlanValidationError(f"GW{gameweek} transfer lists contain duplicates")
        if any(player_id not in week_rows for player_id in selected_ids + incoming_ids + outgoing_ids):
            raise HorizonPlanValidationError(f"GW{gameweek} contains a player absent from its projection")
        if week.captain is None:
            raise HorizonPlanValidationError(f"GW{gameweek} has no captain")
        for player in (
            list(week.resulting_squad)
            + list(week.incoming)
            + list(week.outgoing)
            + list(week.starting_xi)
            + [week.captain]
            + ([week.vice_captain] if week.vice_captain is not None else [])
        ):
            row = week_rows.get(player.player_id)
            if row is None or (
                player.player_name != row.player_name
                or player.position != row.position
                or player.club != row.club
                or player.cost != row.cost
                or not isclose(player.projected_points, row.projected_points, rel_tol=0.0, abs_tol=0.00001)
            ):
                raise HorizonPlanValidationError(f"GW{gameweek} player data is inconsistent with its projection")
        if set(selected_ids) - previous_ids != set(incoming_ids):
            raise HorizonPlanValidationError(f"GW{gameweek} incoming transfers do not match the squad transition")
        if previous_ids - set(selected_ids) != set(outgoing_ids):
            raise HorizonPlanValidationError(f"GW{gameweek} outgoing transfers do not match the squad transition")
        opening_adoption = not current_ids and index == 0
        if (
            week.transfers_made != len(incoming_ids)
            or (opening_adoption and outgoing_ids)
            or (not opening_adoption and len(incoming_ids) != len(outgoing_ids))
        ):
            raise HorizonPlanValidationError(f"GW{gameweek} transfer count is inconsistent")
        if set(incoming_ids) & set(outgoing_ids):
            raise HorizonPlanValidationError(f"GW{gameweek} cannot transfer a player both ways")

        unlimited = pre_season and index == 0
        if week.unlimited_transfers != unlimited:
            raise HorizonPlanValidationError(f"GW{gameweek} unlimited-transfer flag is invalid")
        if week.free_transfers_before != free or not 1 <= free <= 5:
            raise HorizonPlanValidationError(f"GW{gameweek} free-transfer balance is invalid")
        if not unlimited and week.transfers_made > free + paid_cap:
            raise HorizonPlanValidationError(f"GW{gameweek} exceeds the paid-transfer safety cap")
        if unlimited and week.transfers_made > 15:
            raise HorizonPlanValidationError(f"GW{gameweek} exceeds the opening-squad capacity")
        expected_free_after = (
            1 if unlimited else min(5, max(0, free - week.transfers_made) + 1)
        )
        if week.free_transfers_after != expected_free_after:
            raise HorizonPlanValidationError(f"GW{gameweek} free-transfer rollover is invalid")
        if index + 1 < len(plan.gameweeks) and plan.gameweeks[index + 1].free_transfers_before != expected_free_after:
            raise HorizonPlanValidationError(f"GW{gameweek} does not feed the next free-transfer balance")

        expected_hit = 0 if unlimited else max(0, week.transfers_made - free) * 4
        if week.hit_cost != expected_hit:
            raise HorizonPlanValidationError(f"GW{gameweek} hit cost is invalid")

        bank_before = bank
        sale_value = 0
        for player_id in outgoing_ids:
            purchase_price = purchase_book.pop(player_id, None)
            if purchase_price is None:
                raise HorizonPlanValidationError(f"GW{gameweek} sells a player not held in the purchase book")
            cost = week_rows[player_id].cost
            sale_value += cost if cost <= purchase_price else purchase_price + (cost - purchase_price) // 2
        purchase_value = 0
        for player_id in incoming_ids:
            if player_id in purchase_book:
                raise HorizonPlanValidationError(f"GW{gameweek} buys a player already held")
            cost = week_rows[player_id].cost
            purchase_book[player_id] = cost
            purchase_value += cost
        initial_funds = DEFAULT_BUDGET_TENTHS if index == 0 and not current_ids else 0
        expected_bank = bank_before + initial_funds + sale_value - purchase_value
        if getattr(week, "bank_before", bank_before) != bank_before:
            raise HorizonPlanValidationError(f"GW{gameweek} bank-before value is invalid")
        if getattr(week, "sale_value", sale_value) != sale_value:
            raise HorizonPlanValidationError(f"GW{gameweek} sale value is invalid")
        if getattr(week, "purchase_value", purchase_value) != purchase_value:
            raise HorizonPlanValidationError(f"GW{gameweek} purchase value is invalid")
        if week.bank_after != expected_bank or week.bank_after < 0:
            raise HorizonPlanValidationError(f"GW{gameweek} bank accounting is invalid")

        if len(selected_ids) != 15:
            raise HorizonPlanValidationError(f"GW{gameweek} resulting squad is not a full squad")
        squad_players = [
            SquadPlayer(
                id=player.player_id, name=player.player_name, position=player.position,
                club=player.club, cost=player.cost, projected_points=player.projected_points,
            )
            for player in week.resulting_squad
        ]
        legality = validate_squad(SquadRequest(
            players=squad_players,
            budget=sum(player.cost for player in week.resulting_squad) + week.bank_after,
        ))
        if not legality.legal:
            raise HorizonPlanValidationError(f"GW{gameweek} squad is illegal: {'; '.join(legality.errors)}")

        starter_ids = [player.player_id for player in week.starting_xi]
        starter_set = set(starter_ids)
        if len(starter_ids) != 11 or len(starter_set) != 11 or not starter_set <= set(selected_ids):
            raise HorizonPlanValidationError(f"GW{gameweek} starting XI is invalid")
        position_counts = {
            position: sum(week_rows[player_id].position == position for player_id in starter_set)
            for position in ("GK", "DEF", "MID", "FWD")
        }
        if position_counts["GK"] != 1 or not 3 <= position_counts["DEF"] <= 5:
            raise HorizonPlanValidationError(f"GW{gameweek} has an illegal goalkeeper/defender formation")
        if not 2 <= position_counts["MID"] <= 5 or not 1 <= position_counts["FWD"] <= 3:
            raise HorizonPlanValidationError(f"GW{gameweek} has an illegal midfielder/forward formation")
        captain_id = week.captain.player_id
        vice_id = week.vice_captain.player_id if week.vice_captain is not None else None
        ranked_starters = sorted(starter_set, key=lambda player_id: (-week_rows[player_id].projected_points, player_id))
        expected_captain_id = ranked_starters[0]
        expected_vice_id = ranked_starters[1] if len(ranked_starters) > 1 else None
        if settings.objective_mode == "RANK_MODE":
            from aifpl.captaincy_strategy import choose_captain

            choice = choose_captain(week.starting_xi, settings.game_state, settings.template_states)
            expected_captain_id = choice.captain.player_id
            expected_vice_id = choice.vice_captain.player_id
        if captain_id not in starter_set or captain_id != expected_captain_id:
            raise HorizonPlanValidationError(f"GW{gameweek} captain is invalid")
        if vice_id is None or vice_id == captain_id or vice_id != expected_vice_id:
            raise HorizonPlanValidationError(f"GW{gameweek} vice-captain is invalid")
        projected = round(
            sum(week_rows[player_id].projected_points for player_id in starter_set)
            + week_rows[captain_id].projected_points,
            4,
        )
        if not isclose(week.projected_points, projected, rel_tol=0.0, abs_tol=0.00011):
            raise HorizonPlanValidationError(f"GW{gameweek} projected points are invalid")
        if not isclose(week.net_projected_points, round(projected - expected_hit, 4), rel_tol=0.0, abs_tol=0.00011):
            raise HorizonPlanValidationError(f"GW{gameweek} net projected points are invalid")

        audit_week = _audit_week_weight(rows, gameweek, index, settings)
        if not isclose(week.odds_coverage, audit_week.odds_coverage, rel_tol=0.0, abs_tol=0.00011):
            raise HorizonPlanValidationError(f"GW{gameweek} odds coverage is invalid")
        expected_components = _audit_objective_breakdown(
            rows_by_id=week_rows,
            selected_ids=set(selected_ids),
            starter_ids=starter_set,
            captain_id=captain_id,
            transfers_made=week.transfers_made,
            free_transfers_before=free,
            bank_after=week.bank_after,
            bench_floor=audit_bench_floor,
            preferred_player_ids=preferred_player_ids or set(),
            differential_appetite=differential_appetite,
            week=audit_week,
            settings=settings,
            unlimited_transfers=unlimited,
        )
        actual_components = getattr(week, "objective_components", None)
        if not isinstance(actual_components, Mapping):
            raise HorizonPlanValidationError(f"GW{gameweek} has no objective decomposition")
        for key, expected in expected_components.items():
            if key not in actual_components:
                raise HorizonPlanValidationError(f"GW{gameweek} objective is missing {key}")
            _assert_close(float(actual_components[key]), expected, f"GW{gameweek} objective {key}")
        _assert_close(week.objective_net_points, expected_components["total"], f"GW{gameweek} objective total")

        objective_components.append(expected_components)
        total_projected += week.projected_points
        total_hits += expected_hit
        total_net += week.net_projected_points
        previous_ids = set(selected_ids)
        bank = week.bank_after
        free = expected_free_after

    if plan.total_hit_cost != total_hits:
        raise HorizonPlanValidationError("Total hit cost is inconsistent with the weekly plans")
    _assert_close(plan.total_projected_points, round(total_projected, 4), "Total projected points")
    _assert_close(plan.total_net_projected_points, round(total_net, 4), "Total net projected points")
    expected_aggregate = aggregate_objective_components(objective_components)
    actual_aggregate = getattr(plan, "objective_components", None)
    if not isinstance(actual_aggregate, Mapping):
        raise HorizonPlanValidationError("Plan has no objective decomposition")
    for key, expected in expected_aggregate.items():
        if key not in actual_aggregate:
            raise HorizonPlanValidationError(f"Plan objective is missing {key}")
        _assert_close(float(actual_aggregate[key]), expected, f"Plan objective {key}")
    _assert_close(
        getattr(plan, "objective_value", sum(component["total"] for component in objective_components)),
        expected_aggregate["total"],
        "Plan objective total",
    )
