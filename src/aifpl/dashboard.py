from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.hermes import HermesManager, HorizonPlanWeekSnapshot
from aifpl.odds_projections import OddsProjectionStore
from aifpl.scheduler import DeadlineScheduler
from aifpl.scoring import DecisionScorer


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


class DashboardTransfer(BaseModel):
    out_id: int | None = None
    out_name: str | None = None
    in_id: int
    in_name: str


class DashboardHorizonPoint(BaseModel):
    gameweek: int
    projected_points: float
    net_projected_points: float | None = None
    hit_cost: int | None = None
    transfers_made: int | None = None
    free_transfers_before: int | None = None
    bank_after: int | None = None
    odds_coverage: float | None = None
    captain_id: int | None = None
    transfers: list[DashboardTransfer] = Field(default_factory=list)


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


class DashboardConfidence(BaseModel):
    status: str = "unknown"
    calibration: str = "uncalibrated"
    odds_coverage_by_gameweek: dict[int, float] = Field(default_factory=dict)
    evidence_cutoff: datetime | None = None
    max_evidence_age_hours: float | None = None
    projection_catalog: str | None = None


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
    formation: str
    projected_points: float
    projection_available: bool
    players: list[DashboardPlayer]
    transfers: list[DashboardTransfer] = Field(default_factory=list)
    horizon: list[DashboardHorizonPoint] = Field(default_factory=list)
    inputs: list[DashboardInput] = Field(default_factory=list)
    scorecard: DashboardScorecard | None = None
    projection_catalog: str | None = None
    solver_status: str | None = None
    confidence: DashboardConfidence = Field(default_factory=DashboardConfidence)


def build_current_dashboard(root: Path) -> CurrentDashboard:
    from datetime import datetime, timezone

    decision = HermesManager(root).latest_decision()
    schedule = DeadlineScheduler(root).status()
    pre_season = schedule.event == 1 and schedule.deadline > datetime.now(timezone.utc)
    current_players = CurrentPlayerCatalogStore(root).latest_players()
    players_by_id = {player.id: player for player in current_players}
    projections = OddsProjectionStore(root).latest()
    projections_by_key = {(row.player_id, row.gameweek): row for row in projections}

    missing_ids = set(decision.squad.player_ids) - set(players_by_id)
    if missing_ids:
        raise ValueError(f"Current player catalog is missing squad IDs: {sorted(missing_ids)}")

    starting_ids = set(decision.starting_xi_ids)
    squad_rows = [
        _dashboard_player(
            players_by_id[player_id],
            projections_by_key.get((player_id, decision.gameweek)),
            player_id in starting_ids,
            player_id == decision.captain_id,
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
    plan_snapshot = decision.horizon_plan
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
        latest_scorecard = DecisionScorer(root).latest()
        best = max(latest_scorecard.players, key=lambda player: player.actual, default=None)
        scorecard = DashboardScorecard(
            gameweek=latest_scorecard.gameweek,
            projected=latest_scorecard.total_projected,
            actual=latest_scorecard.total_actual,
            delta=round(latest_scorecard.total_actual - latest_scorecard.total_projected, 4),
            best_player_id=best.element if best else None,
            best_player_name=best.name if best else None,
            best_player_actual=best.actual if best else None,
        )
    except FileNotFoundError:
        pass

    inputs = [
        DashboardInput(name="FPL bootstrap", status="ready", detail="Latest official snapshot loaded"),
        DashboardInput(name="Odds projections", status="ready", detail=f"{len(projections)} projection records loaded"),
        DashboardInput(name="Hermes decision", status="ready", detail=f"GW {decision.gameweek} committed"),
    ]
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
        formation=formation,
        projected_points=round(projected_points, 4),
        projection_available=any(row is not None for row in current_projection_rows.values()),
        players=squad_rows,
        transfers=transfers,
        horizon=horizon,
        inputs=inputs,
        scorecard=scorecard,
        projection_catalog=plan_snapshot.projection_catalog if plan_snapshot is not None else None,
        solver_status=plan_snapshot.solver_status if plan_snapshot is not None else None,
        confidence=_dashboard_confidence(root),
    )


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
    return DashboardConfidence(
        status=parameters.get("odds_coverage_status", "unknown"),
        odds_coverage_by_gameweek={int(key): float(value) for key, value in coverage.items()},
        evidence_cutoff=parameters.get("evidence_cutoff"),
        max_evidence_age_hours=parameters.get("max_evidence_age_hours"),
        projection_catalog=path.name,
    )


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
        captain_id=week.captain_id,
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
) -> DashboardPlayer:
    projected_points = float(getattr(projection, "projected_points", 0.0))
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
        selected_by_percent=round(player.selected_by_percent, 4),
        points_per_game=round(player.points_per_game, 4),
        expected_minutes=getattr(projection, "expected_minutes", None),
        start_probability=getattr(projection, "start_probability", None),
        availability_multiplier=getattr(projection, "availability_multiplier", None),
    )
