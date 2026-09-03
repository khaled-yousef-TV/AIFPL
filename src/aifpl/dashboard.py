from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.captaincy_strategy import choose_captain
from aifpl.game_state import (
    GameStateStore,
    calculate_rank_data_age,
    rank_data_is_usable,
)
from aifpl.hermes import HermesDecision, HermesManager, HorizonPlanSnapshot, HorizonPlanWeekSnapshot
from aifpl.live_calibration import calibrated_odds_rows
from aifpl.odds_projections import OddsAdjustedGameweekProjection, OddsProjectionStore
from aifpl.scheduler import DeadlineScheduler
from aifpl.scoring import DecisionScorer
from aifpl.template import (
    PlayerTemplateState,
    TemplateCatalogStore,
    build_exposure_states,
    coverage as template_coverage,
    ownership_source_confidence,
    target_cohort_eo,
)


class DashboardChipAdvice(BaseModel):
    chip: str
    set: int
    status: str
    gameweek: int | None = None
    rationale: str
    confidence: float = 0.5


class DashboardPlayer(BaseModel):
    id: int
    name: str
    position: str
    club: str
    club_id: int
    cost: int
    projected_points: float
    is_starter: bool
    is_captain: bool
    form: float = 0.0
    selected_by_percent: float = 0.0
    points_per_game: float = 0.0
    expected_minutes: float | None = None
    start_probability: float | None = None
    availability_multiplier: float | None = None
    value: float | None = None
    differential_score: float | None = None
    effective_ownership_pct: float | None = None
    expected_captaincy: float | None = None
    template_score: float | None = None
    template_status: str | None = None
    my_exposure: float | None = None
    net_exposure: float | None = None
    rank_swing_potential: float | None = None
    ownership_basis: str = "unavailable"
    ownership_confidence: float | None = None


class DashboardTransfer(BaseModel):
    out_id: int | None = None
    out_name: str | None = None
    in_id: int
    in_name: str


class DashboardMove(BaseModel):
    out_id: int | None = None
    out_name: str | None = None
    in_id: int
    in_name: str
    gameweek: int
    horizon_gain: float | None = None
    hit_cost: int | None = None
    net_gain: float | None = None
    odds_coverage: float | None = None
    out_ownership: float | None = None
    in_ownership: float | None = None
    out_effective_ownership: float | None = None
    in_effective_ownership: float | None = None
    out_ownership_basis: str = "unavailable"
    in_ownership_basis: str = "unavailable"
    out_ownership_confidence: float | None = None
    in_ownership_confidence: float | None = None
    template_exposure_delta: float | None = None
    strategy_classification: str | None = None


class DashboardCaptainOption(BaseModel):
    player_id: int
    name: str
    projected_points: float
    expected_minutes: float | None = None
    start_probability: float | None = None
    is_captain: bool = False
    target_cohort_eo: float | None = None
    net_exposure_if_captained: float | None = None
    strategy_classification: str | None = None
    rank_swing_potential: float | None = None
    ownership_basis: str = "unavailable"
    ownership_confidence: float | None = None


class DashboardHorizonPoint(BaseModel):
    gameweek: int
    projected_points: float
    net_projected_points: float | None = None
    hit_cost: int | None = None
    transfers_made: int | None = None
    free_transfers_before: int | None = None
    bank_after: int | None = None
    odds_coverage: float | None = None
    robustness_score: float | None = None
    unlimited_transfers: bool | None = None
    free_transfers_after: int | None = None
    captain_id: int | None = None
    transfers: list[DashboardTransfer] = Field(default_factory=list)
    objective_components: dict[str, float] = Field(default_factory=dict)


class DashboardInput(BaseModel):
    name: str
    status: str
    detail: str


class DashboardScorecard(BaseModel):
    gameweek: int
    projected: float
    actual: float
    delta: float
    best_player_id: int | None = None
    best_player_name: str | None = None
    best_player_actual: float | None = None
    evaluation_basis: str = "recommendation_only"


class DashboardConfidence(BaseModel):
    status: str = "unknown"
    calibration: str = "uncalibrated"
    odds_coverage_by_gameweek: dict[int, float] = Field(default_factory=dict)
    evidence_cutoff: datetime | None = None
    max_evidence_age_hours: float | None = None
    projection_catalog: str | None = None
    news_status: str = "disabled"


class CurrentDashboard(BaseModel):
    gameweek: int
    season_id: str
    deadline: datetime
    pre_season: bool = False
    action: str
    explanation: str
    model: str
    methodology: str
    bank: int
    free_transfers: int
    captain_id: int
    vice_captain_id: int | None = None
    formation: str
    projected_points: float
    projection_available: bool
    players: list[DashboardPlayer]
    transfers: list[DashboardTransfer] = Field(default_factory=list)
    moves: list[DashboardMove] = Field(default_factory=list)
    captain_options: list[DashboardCaptainOption] = Field(default_factory=list)
    horizon: list[DashboardHorizonPoint] = Field(default_factory=list)
    inputs: list[DashboardInput] = Field(default_factory=list)
    scorecard: DashboardScorecard | None = None
    projection_catalog: str | None = None
    solver_status: str | None = None
    robustness: float | None = None
    confidence: DashboardConfidence = Field(default_factory=DashboardConfidence)
    chip_advice: list[DashboardChipAdvice] = Field(default_factory=list)
    active_chip: str | None = None
    active_chip_set: int | None = None
    objective_mode: str = "POINTS_MODE"
    overall_rank: int | None = None
    target_rank: int | None = None
    gameweeks_remaining: int | None = None
    strategy_status: str = "UNKNOWN"
    risk_level: float | None = None
    template_coverage: float | None = None
    captain_template_coverage: float | None = None
    rank_gap_ratio: float | None = None
    rank_as_of_gameweek: int | None = None
    decision_gameweek: int | None = None
    rank_data_age_gameweeks: int | None = None
    rank_data_stale: bool = False
    rank_mode_degraded: bool = False
    account_sync_status: str = "not_configured"
    account_sync_warning: str | None = None
    account_reconciliation_status: str = "not_checked"
    account_reconciliation_source: str | None = None
    account_reconciliation_warning: str | None = None


def build_current_dashboard(root: Path) -> CurrentDashboard:
    from datetime import datetime, timezone

    schedule = DeadlineScheduler(root).status()
    manager = HermesManager(root)
    try:
        decision = manager.latest_decision(season_id=schedule.season_id, gameweek=schedule.event)
    except FileNotFoundError:
        decision = manager.latest_decision(season_id=schedule.season_id)
    pre_season = schedule.event == 1 and schedule.deadline > datetime.now(timezone.utc)
    plan_snapshot = decision.horizon_plan
    current_players = CurrentPlayerCatalogStore(root).latest_players()
    players_by_id = {player.id: player for player in current_players}
    try:
        game_state = GameStateStore(root).latest(season_id=schedule.season_id)
    except FileNotFoundError:
        game_state = None
    try:
        template_states = {
            row.player_id: row
            for row in TemplateCatalogStore(root).latest(
                season_id=schedule.season_id, gameweek=decision.gameweek,
            ).players
        }
    except FileNotFoundError:
        try:
            template_states = {
                row.player_id: row
                for row in TemplateCatalogStore(root).latest(season_id=schedule.season_id).players
            }
        except FileNotFoundError:
            template_states = {}
    projections = (
        OddsProjectionStore(root).latest(plan_snapshot.projection_catalog)
        if plan_snapshot is not None and plan_snapshot.projection_catalog
        else OddsProjectionStore(root).latest()
    )
    projections_by_key = {(row.player_id, row.gameweek): row for row in projections}

    missing_ids = set(decision.squad.player_ids) - set(players_by_id)
    if missing_ids:
        raise ValueError(f"Current player catalog is missing squad IDs: {sorted(missing_ids)}")

    starting_ids = set(decision.starting_xi_ids)
    exposure_states = build_exposure_states(
        [projections_by_key[(player_id, decision.gameweek)] for player_id in decision.squad.player_ids
         if (player_id, decision.gameweek) in projections_by_key],
        set(decision.squad.player_ids), decision.captain_id,
        decision.active_chip == "triple_captain", template_states,
    )
    exposure_by_id = {row.player_id: row for row in exposure_states}
    squad_rows = [
        _dashboard_player(
            players_by_id[player_id],
            projections_by_key.get((player_id, decision.gameweek)),
            player_id in starting_ids,
            player_id == decision.captain_id,
            template_states.get(player_id), exposure_by_id.get(player_id),
        )
        for player_id in decision.squad.player_ids
    ]
    current_projection_rows = {
        player_id: projections_by_key.get((player_id, decision.gameweek))
        for player_id in decision.starting_xi_ids
    }
    projected_points = sum(
        row.projected_points if row is not None else 0.0
        for row in current_projection_rows.values()
    )
    captain_projection = current_projection_rows.get(decision.captain_id)
    if captain_projection is not None:
        projected_points += captain_projection.projected_points

    position_by_id = {player_id: players_by_id[player_id].position for player_id in starting_ids}
    formation = "-".join(
        str(sum(position == role for position in position_by_id.values()))
        for role in ("DEF", "MID", "FWD")
    )
    names = {player_id: player.name for player_id, player in players_by_id.items()}
    horizon: list[DashboardHorizonPoint]
    if plan_snapshot is not None and plan_snapshot.weeks:
        horizon = [_horizon_point(week, names) for week in plan_snapshot.weeks]
        committed = next(
            (week for week in plan_snapshot.weeks if week.gameweek == decision.gameweek),
            None,
        )
        if committed is not None:
            projected_points = committed.projected_points
    else:
        available_gameweeks = sorted({row.gameweek for row in projections if row.gameweek >= decision.gameweek})[:6]
        horizon = [
            DashboardHorizonPoint(
                gameweek=gameweek,
                projected_points=round(
                    sum(
                        (projections_by_key.get((player_id, gameweek)).projected_points
                         if projections_by_key.get((player_id, gameweek)) is not None else 0.0)
                        for player_id in decision.starting_xi_ids
                    )
                    + (projections_by_key.get((decision.captain_id, gameweek)).projected_points
                       if projections_by_key.get((decision.captain_id, gameweek)) is not None else 0.0),
                    4,
                ),
            )
            for gameweek in available_gameweeks
        ]
    robustness = None
    if plan_snapshot is not None and plan_snapshot.weeks:
        committed = next(
            (week for week in plan_snapshot.weeks if week.gameweek == decision.gameweek),
            None,
        )
        if committed is not None:
            robustness = committed.robustness_score
    account_state = game_state or decision.game_state
    account_sync_status = account_state.account_sync_status if account_state is not None else "not_configured"
    account_sync_warning = account_state.account_sync_warning if account_state is not None else None
    if account_state is None or account_sync_status == "not_configured":
        tick = DeadlineScheduler(root).latest_tick(schedule.season_id, schedule.event)
        if tick is not None:
            account_sync_status = tick.account_sync_status
            account_sync_warning = tick.account_sync_warning
    rank_state = None
    effective_objective_mode = decision.strategy.objective_mode
    rank_mode_degraded = account_state.rank_mode_degraded if account_state is not None else False
    if decision.strategy.objective_mode == "RANK_MODE" and rank_data_is_usable(
        account_state, decision_gameweek=decision.gameweek,
    ):
        rank_state = account_state
    elif decision.strategy.objective_mode == "RANK_MODE":
        effective_objective_mode = "POINTS_MODE"
        rank_mode_degraded = True
    if rank_state is not None and rank_state.objective_mode != "RANK_MODE":
        rank_state = rank_state.model_copy(update={"objective_mode": "RANK_MODE"})
    moves = (
        _dashboard_moves(plan_snapshot, projections_by_key, names, rank_state, template_states)
        if plan_snapshot is not None else []
    )
    captain_options = (
        _dashboard_captain_options(projections_by_key, decision, names, rank_state, template_states)
        if plan_snapshot is not None else []
    )
    transfers = [
        DashboardTransfer(
            out_id=out_id,
            out_name=names.get(out_id),
            in_id=in_id,
            in_name=names.get(in_id, f"#{in_id}"),
        )
        for out_id, in_id in zip(decision.transfers_out, decision.transfers_in)
    ]
    if not transfers and decision.action == "adopt_initial":
        transfers = [
            DashboardTransfer(in_id=player_id, in_name=names.get(player_id, f"#{player_id}"))
            for player_id in decision.transfers_in
        ]

    scorecard = None
    try:
        latest_scorecard = next(
            record for record in DecisionScorer(root).recent(100)
            if record.season_id == schedule.season_id
        )
        best = max(latest_scorecard.players, key=lambda player: player.actual, default=None)
        scorecard = DashboardScorecard(
            gameweek=latest_scorecard.gameweek,
            projected=latest_scorecard.total_projected,
            actual=latest_scorecard.total_actual,
            delta=round(latest_scorecard.total_actual - latest_scorecard.total_projected, 4),
            best_player_id=best.element if best else None,
            best_player_name=best.name if best else None,
            best_player_actual=best.actual if best else None,
            evaluation_basis=latest_scorecard.evaluation_basis,
        )
    except FileNotFoundError:
        pass

    inputs = [
        DashboardInput(name="FPL bootstrap", status="ready", detail="Latest official snapshot loaded"),
        DashboardInput(name="Odds projections", status="ready", detail=f"{len(projections)} projection records loaded"),
        DashboardInput(name="Hermes decision", status="ready", detail=f"GW {decision.gameweek} committed"),
    ]
    inputs.append(DashboardInput(
        name="Account sync",
        status=account_sync_status,
        detail=account_sync_warning or "Latest public account state loaded",
    ))
    reconciliation_status = account_state.account_reconciliation_status if account_state is not None else "not_checked"
    reconciliation_warning = account_state.account_reconciliation_warning if account_state is not None else None
    if reconciliation_status != "not_checked":
        inputs.append(DashboardInput(
            name="Squad reconciliation",
            status="warning" if reconciliation_status != "matched" else "ready",
            detail=reconciliation_warning or reconciliation_status,
        ))
    if plan_snapshot is not None and plan_snapshot.projection_catalog:
        inputs.append(
            DashboardInput(name="Projection catalog", status="ready", detail=plan_snapshot.projection_catalog),
        )

    return CurrentDashboard(
        gameweek=decision.gameweek,
        season_id=decision.season_id,
        deadline=schedule.deadline,
        pre_season=pre_season,
        action=decision.action,
        explanation=decision.explanation,
        model=decision.model,
        methodology=decision.backend_methodology,
        bank=decision.squad.bank,
        free_transfers=decision.squad.free_transfers,
        captain_id=decision.captain_id,
        vice_captain_id=decision.vice_captain_id,
        formation=formation,
        projected_points=round(projected_points, 4),
        projection_available=any(row is not None for row in current_projection_rows.values()),
        players=squad_rows,
        transfers=transfers,
        moves=moves,
        captain_options=captain_options,
        horizon=horizon,
        inputs=inputs,
        scorecard=scorecard,
        projection_catalog=plan_snapshot.projection_catalog if plan_snapshot is not None else None,
        solver_status=plan_snapshot.solver_status if plan_snapshot is not None else None,
        robustness=robustness,
        confidence=_dashboard_confidence(root),
        chip_advice=_dashboard_chip_advice(root, decision.season_id),
        active_chip=decision.active_chip,
        active_chip_set=decision.active_chip_set,
        objective_mode=effective_objective_mode,
        overall_rank=account_state.overall_rank if account_state is not None else None,
        target_rank=account_state.target_rank if account_state is not None else None,
        gameweeks_remaining=account_state.gameweeks_remaining if account_state is not None else None,
        strategy_status=account_state.strategy_status if account_state is not None else "UNKNOWN",
        risk_level=account_state.risk_level if account_state is not None else None,
        template_coverage=(
            account_state.template_coverage if account_state is not None and account_state.template_coverage is not None
            else template_coverage(template_states.values(), decision.squad.player_ids)
        ),
        captain_template_coverage=(
            account_state.captain_template_coverage if account_state is not None and account_state.captain_template_coverage is not None
            else _captain_template_coverage(template_states.get(decision.captain_id))
        ),
        rank_gap_ratio=account_state.rank_gap_ratio if account_state is not None else None,
        rank_as_of_gameweek=account_state.rank_as_of_gameweek if account_state is not None else None,
        decision_gameweek=account_state.decision_gameweek if account_state is not None else decision.gameweek,
        rank_data_age_gameweeks=(
            calculate_rank_data_age(
                account_state.rank_as_of_gameweek,
                decision.gameweek,
            )
            if account_state is not None and account_state.rank_data_available
            else None
        ),
        rank_data_stale=account_state.rank_data_stale if account_state is not None else False,
        rank_mode_degraded=rank_mode_degraded,
        account_sync_status=account_sync_status,
        account_sync_warning=account_sync_warning,
        account_reconciliation_status=reconciliation_status,
        account_reconciliation_source=(
            account_state.account_reconciliation_source if account_state is not None else None
        ),
        account_reconciliation_warning=reconciliation_warning,
    )


def _dashboard_chip_advice(root: Path, season_id: str | None = None) -> list[DashboardChipAdvice]:
    try:
        from aifpl.chips import ChipAdviceStore

        advice = ChipAdviceStore(root).latest()
    except FileNotFoundError:
        return []
    if season_id is not None and advice.season_id != season_id:
        return []
    return [
        DashboardChipAdvice(
            chip=item.chip, set=item.set, status=item.status, gameweek=item.gameweek,
            rationale=item.rationale, confidence=item.confidence,
        )
        for item in advice.recommendations
    ]


def _dashboard_confidence(root: Path) -> DashboardConfidence:
    try:
        path = OddsProjectionStore(root).latest_path()
    except FileNotFoundError:
        return DashboardConfidence()
    manifest_path = path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        return DashboardConfidence(projection_catalog=path.name)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DashboardConfidence(projection_catalog=path.name)
    parameters = manifest.get("parameters", {})
    coverage = parameters.get("odds_coverage_by_gameweek") or {}
    calibration = "uncalibrated"
    try:
        _, profile = calibrated_odds_rows(root, path.name)
        if profile is not None:
            calibration = profile.policy_version if profile.status == "active" else profile.status
    except (FileNotFoundError, ValueError):
        pass
    return DashboardConfidence(
        status=parameters.get("odds_coverage_status", "unknown"),
        calibration=calibration,
        odds_coverage_by_gameweek={int(key): float(value) for key, value in coverage.items()},
        evidence_cutoff=parameters.get("evidence_cutoff"),
        max_evidence_age_hours=parameters.get("max_evidence_age_hours"),
        projection_catalog=path.name,
        news_status=_news_status(root),
    )


def _news_status(root: Path) -> str:
    try:
        from aifpl.tavily_news import TavilyNewsStore

        catalog = TavilyNewsStore(root).latest_payload()
    except FileNotFoundError:
        return "disabled"
    assessments = catalog.get("assessments", [])
    if not assessments:
        return "disabled"
    if any(assessment.get("start_probability_cap") is not None for assessment in assessments):
        return "adjusted"
    if any(assessment.get("status") == "watch" for assessment in assessments):
        return "watch"
    return "clear"


def _dashboard_moves(
    plan_snapshot: HorizonPlanSnapshot,
    projections_by_key: dict[tuple[int, int], OddsAdjustedGameweekProjection],
    names: dict[int, str],
    game_state: object | None = None,
    template_states: dict[int, PlayerTemplateState] | None = None,
) -> list[DashboardMove]:
    gameweeks = [week.gameweek for week in plan_snapshot.weeks]
    moves: list[DashboardMove] = []
    for week in plan_snapshot.weeks:
        if not week.outgoing_ids or not week.incoming_ids:
            continue
        allocated_hit = week.hit_cost / max(1, week.transfers_made)
        for out_id, in_id in zip(week.outgoing_ids, week.incoming_ids):
            gain = sum(
                projections_by_key[(in_id, gameweek)].projected_points
                - projections_by_key[(out_id, gameweek)].projected_points
                for gameweek in gameweeks
                if (in_id, gameweek) in projections_by_key and (out_id, gameweek) in projections_by_key
            )
            out_row = projections_by_key.get((out_id, week.gameweek))
            in_row = projections_by_key.get((in_id, week.gameweek))
            out_eo, out_basis, out_confidence = _row_ownership_info(out_row, out_id, template_states)
            in_eo, in_basis, in_confidence = _row_ownership_info(in_row, in_id, template_states)
            moves.append(DashboardMove(
                out_id=out_id, out_name=names.get(out_id), in_id=in_id, in_name=names.get(in_id, f"#{in_id}"),
                gameweek=week.gameweek, horizon_gain=round(gain, 4), hit_cost=week.hit_cost,
                net_gain=round(gain - allocated_hit, 4), odds_coverage=week.odds_coverage,
                out_ownership=out_row.selected_by_percent if out_row is not None else None,
                in_ownership=in_row.selected_by_percent if in_row is not None else None,
                out_effective_ownership=out_eo,
                in_effective_ownership=in_eo,
                out_ownership_basis=out_basis,
                in_ownership_basis=in_basis,
                out_ownership_confidence=out_confidence,
                in_ownership_confidence=in_confidence,
                template_exposure_delta=round(in_eo - out_eo, 4) if in_eo is not None and out_eo is not None else None,
                strategy_classification=_move_classification(game_state, out_eo, in_eo),
            ))
    return moves


def _dashboard_captain_options(
    projections_by_key: dict[tuple[int, int], OddsAdjustedGameweekProjection],
    decision: HermesDecision,
    names: dict[int, str],
    game_state: object | None = None,
    template_states: dict[int, PlayerTemplateState] | None = None,
) -> list[DashboardCaptainOption]:
    gameweek = decision.gameweek
    candidates = [
        (player_id, projections_by_key[(player_id, gameweek)])
        for player_id in decision.squad.player_ids
        if (player_id, gameweek) in projections_by_key
    ]
    if game_state is not None and getattr(game_state, "rank_data_available", False):
        choice = choose_captain(
            [row for _, row in candidates], game_state, template_states,
            decision.active_chip == "triple_captain",
        )
        ranked_ids = [option.player_id for option in choice.options]
        candidates.sort(key=lambda item: ranked_ids.index(item[0]))
        option_by_id = {option.player_id: option for option in choice.options}
    else:
        candidates.sort(key=lambda item: (-item[1].projected_points, item[0]))
        option_by_id = {}
    return [
        DashboardCaptainOption(
            player_id=player_id, name=names.get(player_id, f"#{player_id}"),
            projected_points=round(row.projected_points, 4),
            expected_minutes=row.expected_minutes,
            start_probability=row.start_probability,
            is_captain=player_id == decision.captain_id,
            target_cohort_eo=option_by_id[player_id].target_cohort_eo if player_id in option_by_id else _row_cohort_eo(row, player_id, template_states),
            net_exposure_if_captained=option_by_id[player_id].net_exposure if player_id in option_by_id else _captain_net_exposure(
                row, player_id, template_states, decision.active_chip == "triple_captain",
            ),
            strategy_classification=option_by_id[player_id].classification if player_id in option_by_id else None,
            rank_swing_potential=option_by_id[player_id].rank_swing_potential if player_id in option_by_id else None,
            ownership_basis=option_by_id[player_id].ownership_basis if player_id in option_by_id else _row_ownership_info(
                row, player_id, template_states,
            )[1],
            ownership_confidence=option_by_id[player_id].ownership_confidence if player_id in option_by_id else _row_ownership_info(
                row, player_id, template_states,
            )[2],
        )
        for player_id, row in candidates[:3]
    ]


def _horizon_point(week: HorizonPlanWeekSnapshot, names: dict[int, str]) -> DashboardHorizonPoint:
    return DashboardHorizonPoint(
        gameweek=week.gameweek,
        projected_points=week.projected_points,
        net_projected_points=week.net_projected_points,
        hit_cost=week.hit_cost,
        transfers_made=week.transfers_made,
        free_transfers_before=week.free_transfers_before,
        bank_after=week.bank_after,
        odds_coverage=week.odds_coverage,
        robustness_score=week.robustness_score,
        unlimited_transfers=week.unlimited_transfers,
        free_transfers_after=week.free_transfers_after,
        captain_id=week.captain_id,
        objective_components=week.objective_components,
        transfers=[
            DashboardTransfer(
                out_id=out_id,
                out_name=names.get(out_id),
                in_id=in_id,
                in_name=names.get(in_id, f"#{in_id}"),
            )
            for out_id, in_id in zip(week.outgoing_ids, week.incoming_ids)
        ],
    )


def _dashboard_player(
    player: CurrentPlayer,
    projection: object,
    is_starter: bool,
    is_captain: bool,
    template: PlayerTemplateState | None = None,
    exposure: object | None = None,
) -> DashboardPlayer:
    projected_points = float(getattr(projection, "projected_points", 0.0))
    ownership = player.selected_by_percent
    _, ownership_basis, ownership_confidence = _row_ownership_info(
        projection, player.id, {template.player_id: template} if template is not None else None,
    )
    if exposure is not None:
        ownership_basis = getattr(exposure, "basis", ownership_basis)
        ownership_confidence = getattr(exposure, "ownership_confidence", ownership_confidence)
    return DashboardPlayer(
        id=player.id,
        name=player.name,
        position=player.position,
        club=player.club,
        club_id=player.club_id,
        cost=player.cost,
        projected_points=round(projected_points, 4),
        is_starter=is_starter,
        is_captain=is_captain,
        form=round(player.form, 4),
        selected_by_percent=round(ownership, 4),
        points_per_game=round(player.points_per_game, 4),
        expected_minutes=getattr(projection, "expected_minutes", None),
        start_probability=getattr(projection, "start_probability", None),
        availability_multiplier=getattr(projection, "availability_multiplier", None),
        value=round(projected_points / (player.cost / 10), 4) if player.cost > 0 else None,
        differential_score=round(projected_points * (1 - ownership / 100), 4),
        effective_ownership_pct=_first_number(
            getattr(template, "effective_ownership", None),
            getattr(projection, "effective_ownership_pct", None),
        ),
        expected_captaincy=_first_number(
            getattr(template, "expected_captaincy", None),
            getattr(projection, "expected_captaincy", None),
        ),
        template_score=getattr(template, "template_score", None),
        template_status=getattr(template, "template_status", None),
        my_exposure=getattr(exposure, "my_exposure", None),
        net_exposure=getattr(exposure, "net_exposure", None),
        rank_swing_potential=getattr(exposure, "rank_swing_potential", None),
        ownership_basis=ownership_basis,
        ownership_confidence=ownership_confidence,
    )


def _captain_template_coverage(template: PlayerTemplateState | None) -> float | None:
    if template is None or template.expected_captaincy is None:
        return None
    return round(min(100.0, template.expected_captaincy) / 100, 4)


def _first_number(*values: object) -> float | None:
    for value in values:
        if value is not None:
            return float(value)
    return None


def _row_cohort_eo(
    row: OddsAdjustedGameweekProjection | None,
    player_id: int,
    template_states: dict[int, PlayerTemplateState] | None,
) -> float | None:
    value, _, _ = _row_ownership_info(row, player_id, template_states)
    return value


def _row_ownership_info(
    row: OddsAdjustedGameweekProjection | object | None,
    player_id: int,
    template_states: dict[int, PlayerTemplateState] | None,
) -> tuple[float | None, str, float | None]:
    if row is not None and getattr(row, "effective_ownership_pct", None) is not None:
        return (
            float(getattr(row, "effective_ownership_pct")),
            "effective_ownership",
            ownership_source_confidence("effective_ownership"),
        )
    value, basis = target_cohort_eo(
        player_id,
        (template_states or {}).get(player_id),
        getattr(row, "selected_by_percent", None) if row is not None else None,
        getattr(row, "expected_captaincy", None) if row is not None else None,
    )
    confidence = ownership_source_confidence(basis) if value is not None else None
    return value, basis, confidence


def _captain_net_exposure(
    row: OddsAdjustedGameweekProjection,
    player_id: int,
    template_states: dict[int, PlayerTemplateState] | None,
    triple_captain: bool = False,
) -> float | None:
    field_eo = _row_cohort_eo(row, player_id, template_states)
    own_exposure = 300.0 if triple_captain else 200.0
    return round(own_exposure - field_eo, 4) if field_eo is not None else None


def _move_classification(
    game_state: object | None,
    out_eo: float | None,
    in_eo: float | None,
) -> str | None:
    if game_state is None or not getattr(game_state, "rank_data_available", False) or out_eo is None or in_eo is None:
        return None
    delta = in_eo - out_eo
    status = getattr(game_state, "strategy_status", "UNKNOWN")
    if status == "PROTECT_POSITION" and delta < 0:
        return "UNNECESSARY_RISK"
    if status == "BEHIND_TARGET" and delta < 0:
        return "CALCULATED_ATTACK"
    if status == "BEHIND_TARGET":
        return "SELECTIVE_LEVERAGE"
    return "BALANCED_PLAY"
