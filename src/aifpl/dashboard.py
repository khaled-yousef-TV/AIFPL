from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.hermes import HermesManager
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


class DashboardTransfer(BaseModel):
    out_id: int | None = None
    out_name: str | None = None
    in_id: int
    in_name: str


class DashboardHorizonPoint(BaseModel):
    gameweek: int
    projected_points: float


class DashboardInput(BaseModel):
    name: str
    status: str
    detail: str


class DashboardScorecard(BaseModel):
    gameweek: int
    projected: float
    actual: float
    delta: float


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
    names = {player_id: player.name for player_id, player in players_by_id.items()}
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
        scorecard = DashboardScorecard(
            gameweek=latest_scorecard.gameweek,
            projected=latest_scorecard.total_projected,
            actual=latest_scorecard.total_actual,
            delta=round(latest_scorecard.total_actual - latest_scorecard.total_projected, 4),
        )
    except FileNotFoundError:
        pass

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
        inputs=[
            DashboardInput(name="FPL bootstrap", status="ready", detail="Latest official snapshot loaded"),
            DashboardInput(name="Odds projections", status="ready", detail=f"{len(projections)} projection records loaded"),
            DashboardInput(name="Hermes decision", status="ready", detail=f"GW {decision.gameweek} committed"),
        ],
        scorecard=scorecard,
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
    )
