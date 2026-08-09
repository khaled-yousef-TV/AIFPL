from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from aifpl.config import cors_origins, data_dir
from aifpl.calibration import CalibrationReport, ErrorMetrics, compare_prediction_runs, fit_walk_forward_calibration
from aifpl.current import CurrentPlayer, CurrentPlayerCatalog, CurrentPlayerCatalogStore
from aifpl.current_projections import CurrentPlayerProjection, CurrentProjectionStore, ProjectionCatalog
from aifpl.dashboard import CurrentDashboard, build_current_dashboard
from aifpl.fixture_projections import FixtureGameweekProjection, FixtureProjectionCatalog, FixtureProjectionStore, build_fixture_gameweek_projections
from aifpl.fixtures import CurrentFixture, CurrentFixtureCatalogStore, FixtureCatalog
from aifpl.fpl import FplClient, FplSourceError
from aifpl.historical import HistoricalSeasonImporter, HistoricalSourceError, SeasonImportSummary
from aifpl.health import SourceHealthChecker, SourceHealthReport
from aifpl.hermes import HermesDecision, HermesManager, HermesRunResult, HermesState
from aifpl.horizon_transfers import HorizonSquadState, HorizonTransferPlan, plan_horizon_transfers
from aifpl.optimizer import OptimizedSquad, SquadOptimizationError, optimize_squad
from aifpl.odds import NormalizedMatchOdds, OddsSnapshotStore, OddsSnapshotSummary, OddsSourceError, TheOddsApiClient
from aifpl.odds_matching import FixtureOddsConsensus, FixtureOddsConsensusCatalog, FixtureOddsConsensusStore, load_team_aliases
from aifpl.odds_projections import OddsAdjustedGameweekProjection, OddsProjectionCatalog, OddsProjectionStore, build_odds_adjusted_projections
from aifpl.projection_catalogs import ProjectionSource, load_projection_candidates
from aifpl.player_evidence import PlayerEvidence, PlayerEvidenceCatalog, PlayerEvidenceStore
from aifpl.market_odds import EventMarketCatalog, EventMarketStore
from aifpl.market_signals import MarketSignalCatalog, MarketSignalStore
from aifpl.projections import BacktestSummary, BaselineBacktester
from aifpl.refresh import CurrentDataRefreshJob, RefreshJobError, RefreshJobResult
from aifpl.scheduler import DeadlineScheduler, DeadlineStatus, SchedulerTickError, SchedulerTickResult
from aifpl.rules import LineupRecommendation, SquadRequest, SquadValidation, TransferCostRequest, select_best_lineup, transfer_hit_cost, validate_squad
from aifpl.transfers import CurrentSquadState, TransferPlan, plan_transfers
from aifpl.xg_projections import XgXaProjection, XgXaProjectionCatalog, XgXaProjectionStore, elapsed_gameweeks
from aifpl.snapshots import SnapshotNotFoundError, SnapshotStore
from aifpl.security import valid_admin_key
from aifpl.teams import CurrentTeam, CurrentTeamCatalogStore, team_logo_path

app = FastAPI(title="AIFPL Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"]
)


@app.middleware("http")
async def protect_mutating_operations(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not os.environ.get("AIFPL_ADMIN_API_KEY"):
            return JSONResponse(status_code=503, content={"detail": "AIFPL_ADMIN_API_KEY is not configured"})
        if not valid_admin_key(request.headers.get("X-AIFPL-Admin-Key")):
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing admin API key"})
    return await call_next(request)


class LatestSnapshotResponse(BaseModel):
    path: str
    summary: dict[str, object]


class GameweekRangeRequest(BaseModel):
    start_gameweek: int = Field(default=1, ge=1, le=38)
    end_gameweek: int = Field(default=38, ge=1, le=38)


class BacktestRequest(BaseModel):
    start_gameweek: int = Field(ge=1, le=38)
    end_gameweek: int = Field(ge=1, le=38)
    data_cutoff: datetime
    window: int = Field(default=5, ge=1, le=38)
    import_id: str | None = None


class RefreshJobRequest(GameweekRangeRequest):
    budget: int = Field(default=1000, ge=0)


class CalibrationRequest(BaseModel):
    predictions_path: str
    train_end_gameweek: int = Field(ge=1, le=38)
    evaluation_start_gameweek: int = Field(ge=1, le=38)


class CalibrationComparisonRequest(BaseModel):
    prediction_paths: list[str] = Field(min_length=2)


class HermesMigrationRequest(BaseModel):
    purchase_prices: dict[int, int]
    gameweek: int = Field(ge=1, le=38)
    season_id: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/health/sources/check", response_model=SourceHealthReport)
def check_source_health() -> SourceHealthReport:
    try:
        return SourceHealthChecker(data_dir()).run()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/health/sources", response_model=SourceHealthReport)
def latest_source_health() -> SourceHealthReport:
    try:
        return SourceHealthChecker(data_dir()).latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/hermes/run", response_model=HermesRunResult, status_code=201)
def run_hermes() -> HermesRunResult:
    try:
        return HermesManager(data_dir()).run_current()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/hermes/state", response_model=HermesState)
def latest_hermes_state() -> HermesState:
    try:
        state = HermesManager(data_dir()).latest_state()
        assert state is not None
        return state
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/hermes/decisions/latest", response_model=HermesDecision)
def latest_hermes_decision() -> HermesDecision:
    try:
        return HermesManager(data_dir()).latest_decision()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/hermes/migrate", response_model=HermesRunResult, status_code=201)
def migrate_hermes_state(request: HermesMigrationRequest) -> HermesRunResult:
    try:
        return HermesManager(data_dir()).migrate_legacy_state(
            request.purchase_prices, request.gameweek, request.season_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/config/team-aliases")
def effective_team_aliases() -> dict[str, object]:
    try:
        aliases, path = load_team_aliases(data_dir())
        return {"aliases": aliases, "configuration_path": str(path) if path else None}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/jobs/refresh/current", response_model=RefreshJobResult, status_code=201)
def refresh_current_data(request: RefreshJobRequest) -> RefreshJobResult:
    try:
        return CurrentDataRefreshJob(data_dir()).run(request.start_gameweek, request.end_gameweek, request.budget)
    except RefreshJobError as exc:
        raise HTTPException(status_code=502, detail=exc.result.model_dump(mode="json")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/jobs/refresh/current/latest", response_model=RefreshJobResult)
def latest_refresh_job() -> RefreshJobResult:
    try:
        return CurrentDataRefreshJob(data_dir()).latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/calibration/backtests", response_model=CalibrationReport, status_code=201)
def calibrate_backtest(request: CalibrationRequest) -> CalibrationReport:
    try:
        path = Path(request.predictions_path)
        if not path.resolve().is_relative_to(data_dir().resolve()):
            raise ValueError("predictions_path must be below AIFPL_DATA_DIR")
        return fit_walk_forward_calibration(
            data_dir(), path, request.train_end_gameweek, request.evaluation_start_gameweek,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/calibration/compare", response_model=dict[str, ErrorMetrics])
def compare_backtests(request: CalibrationComparisonRequest) -> dict[str, ErrorMetrics]:
    try:
        paths = [Path(value) for value in request.prediction_paths]
        if any(not path.resolve().is_relative_to(data_dir().resolve()) for path in paths):
            raise ValueError("All prediction paths must be below AIFPL_DATA_DIR")
        return compare_prediction_runs(data_dir(), paths)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/evidence/players/build", response_model=PlayerEvidenceCatalog, status_code=201)
def build_player_evidence() -> PlayerEvidenceCatalog:
    try:
        return PlayerEvidenceStore(data_dir()).build()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/evidence/players", response_model=list[PlayerEvidence])
def latest_player_evidence(limit: int = Query(100, ge=1, le=5000)) -> list[PlayerEvidence]:
    try:
        return PlayerEvidenceStore(data_dir()).latest()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/odds/epl/event-markets", response_model=EventMarketCatalog, status_code=201)
def fetch_event_markets() -> EventMarketCatalog:
    try:
        event_ids = [row.odds_event_id for row in FixtureOddsConsensusStore(data_dir()).latest()]
        return EventMarketStore(data_dir()).fetch(event_ids)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OddsSourceError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/odds/epl/market-signals", response_model=MarketSignalCatalog, status_code=201)
def build_market_signals() -> MarketSignalCatalog:
    try:
        return MarketSignalStore(data_dir()).build()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/scheduler/status", response_model=DeadlineStatus)
def scheduler_status() -> DeadlineStatus:
    try:
        return DeadlineScheduler(data_dir()).status()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/scheduler/tick", response_model=SchedulerTickResult)
def scheduler_tick(force: bool = Query(False)) -> SchedulerTickResult:
    try:
        return DeadlineScheduler(data_dir()).tick(force=force)
    except SchedulerTickError as exc:
        raise HTTPException(status_code=502, detail=exc.result.model_dump(mode="json")) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/snapshots/latest", response_model=LatestSnapshotResponse)
def latest_snapshot() -> LatestSnapshotResponse:
    try:
        path, summary = SnapshotStore(data_dir()).latest_bootstrap()
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return LatestSnapshotResponse(path=str(path), summary=summary.model_dump(mode="json"))


@app.get("/snapshots/fpl/bootstrap/latest", response_model=LatestSnapshotResponse)
def latest_bootstrap_snapshot() -> LatestSnapshotResponse:
    return latest_snapshot()


@app.get("/snapshots/as-of", response_model=LatestSnapshotResponse)
def snapshot_as_of(at: datetime = Query(description="Timezone-aware ISO-8601 cutoff")) -> LatestSnapshotResponse:
    try:
        path, summary = SnapshotStore(data_dir()).bootstrap_before(at)
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LatestSnapshotResponse(path=str(path), summary=summary.model_dump(mode="json"))


@app.get("/snapshots/fpl/bootstrap/as-of", response_model=LatestSnapshotResponse)
def bootstrap_snapshot_as_of(at: datetime = Query(description="Timezone-aware ISO-8601 cutoff")) -> LatestSnapshotResponse:
    return snapshot_as_of(at)


@app.post("/snapshots/fpl/bootstrap", response_model=LatestSnapshotResponse, status_code=201)
def fetch_bootstrap_snapshot() -> LatestSnapshotResponse:
    try:
        path, summary = SnapshotStore(data_dir()).save_bootstrap(asyncio.run(FplClient().fetch_bootstrap()))
        return LatestSnapshotResponse(path=str(path), summary=summary.model_dump(mode="json"))
    except FplSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/snapshots/fpl/fixtures", response_model=LatestSnapshotResponse, status_code=201)
def fetch_fixture_snapshot() -> LatestSnapshotResponse:
    try:
        path, summary = SnapshotStore(data_dir()).save_fixtures(asyncio.run(FplClient().fetch_fixtures()))
        return LatestSnapshotResponse(path=str(path), summary=summary.model_dump(mode="json"))
    except FplSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/snapshots/fpl/fixtures/latest", response_model=LatestSnapshotResponse)
def latest_fixture_snapshot() -> LatestSnapshotResponse:
    try:
        path, summary = SnapshotStore(data_dir()).latest_fixtures()
        return LatestSnapshotResponse(path=str(path), summary=summary.model_dump(mode="json"))
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/snapshots/fpl/events/{event}", response_model=LatestSnapshotResponse, status_code=201)
def fetch_event_snapshot(event: int) -> LatestSnapshotResponse:
    if event < 1:
        raise HTTPException(status_code=422, detail="event must be at least 1")
    try:
        path, summary = SnapshotStore(data_dir()).save_event_live(event, asyncio.run(FplClient().fetch_event_live(event)))
        return LatestSnapshotResponse(path=str(path), summary=summary.model_dump(mode="json"))
    except FplSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/snapshots/fpl/events/{event}/latest", response_model=LatestSnapshotResponse)
def latest_event_snapshot(event: int) -> LatestSnapshotResponse:
    try:
        path, summary = SnapshotStore(data_dir()).latest_event_live(event)
        return LatestSnapshotResponse(path=str(path), summary=summary.model_dump(mode="json"))
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/historical/seasons/{season}", response_model=SeasonImportSummary)
def historical_season_summary(season: str) -> SeasonImportSummary:
    try:
        return HistoricalSeasonImporter(data_dir()).summary(season)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/historical/seasons/{season}/imports", response_model=SeasonImportSummary, status_code=201)
def import_historical_season(season: str, request: GameweekRangeRequest) -> SeasonImportSummary:
    try:
        return HistoricalSeasonImporter(data_dir()).import_season(season, request.start_gameweek, request.end_gameweek)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HistoricalSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/historical/seasons/{season}/backtests/baseline", response_model=BacktestSummary, status_code=201)
def run_baseline_backtest(season: str, request: BacktestRequest) -> BacktestSummary:
    try:
        importer = HistoricalSeasonImporter(data_dir())
        import_id = request.import_id or importer.latest_import_id(season)
        return BaselineBacktester(data_dir()).run(
            season, import_id, request.start_gameweek, request.end_gameweek, request.data_cutoff, request.window,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/squad/validate", response_model=SquadValidation)
def validate_squad_endpoint(squad: SquadRequest) -> SquadValidation:
    return validate_squad(squad)


@app.post("/squad/lineup", response_model=LineupRecommendation)
def squad_lineup(squad: SquadRequest) -> LineupRecommendation:
    try:
        return select_best_lineup(squad)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/players/current", response_model=list[CurrentPlayer])
def current_players(limit: int = Query(20, ge=1, le=1000)) -> list[CurrentPlayer]:
    try:
        return CurrentPlayerCatalogStore(data_dir()).latest_players()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/dashboard/current", response_model=CurrentDashboard)
def current_dashboard() -> CurrentDashboard:
    try:
        return build_current_dashboard(data_dir())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/teams/current", response_model=list[CurrentTeam])
def current_teams() -> list[CurrentTeam]:
    try:
        return CurrentTeamCatalogStore(data_dir()).latest()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/teams/{team_id}/logo.png", response_class=FileResponse)
def current_team_logo(team_id: int) -> FileResponse:
    try:
        team_ids = {team.id for team in CurrentTeamCatalogStore(data_dir()).latest()}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if team_id not in team_ids:
        raise HTTPException(status_code=404, detail=f"Unknown current team: {team_id}")
    path = team_logo_path(team_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"No logo asset exists for team: {team_id}")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.post("/catalogs/current/players", response_model=CurrentPlayerCatalog, status_code=201)
def normalize_current_players() -> CurrentPlayerCatalog:
    try:
        return CurrentPlayerCatalogStore(data_dir()).normalize_latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/catalogs/current/fixtures", response_model=FixtureCatalog, status_code=201)
def normalize_current_fixtures() -> FixtureCatalog:
    try:
        return CurrentFixtureCatalogStore(data_dir()).normalize_latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/fixtures/current", response_model=list[CurrentFixture])
def current_fixtures(limit: int = Query(20, ge=1, le=1000)) -> list[CurrentFixture]:
    try:
        return CurrentFixtureCatalogStore(data_dir()).latest()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/projections/current", response_model=list[CurrentPlayerProjection])
def current_projections(limit: int = Query(20, ge=1, le=1000)) -> list[CurrentPlayerProjection]:
    try:
        return CurrentProjectionStore(data_dir()).latest()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/squad/optimize/current", response_model=OptimizedSquad)
def optimize_current_squad(
    budget: int = Query(1000, ge=0), projection_source: ProjectionSource = Query(ProjectionSource.CURRENT),
    catalog_id: str | None = Query(None),
    differential_appetite: float = Query(0.0, ge=0, le=1),
) -> OptimizedSquad:
    try:
        return optimize_squad(
            load_projection_candidates(data_dir(), projection_source, catalog_id), budget,
            differential_appetite=differential_appetite,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (SquadOptimizationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/transfers/plan", response_model=TransferPlan)
def transfer_plan(
    state: CurrentSquadState, projection_source: ProjectionSource = Query(ProjectionSource.CURRENT),
    catalog_id: str | None = Query(None),
) -> TransferPlan:
    try:
        return plan_transfers(load_projection_candidates(data_dir(), projection_source, catalog_id), state)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (SquadOptimizationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/transfers/plan/horizon", response_model=HorizonTransferPlan)
def horizon_transfer_plan(state: HorizonSquadState, catalog_id: str | None = Query(None)) -> HorizonTransferPlan:
    try:
        return plan_horizon_transfers(OddsProjectionStore(data_dir()).latest(catalog_id), state)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (SquadOptimizationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/projections/fixtures", response_model=list[FixtureGameweekProjection])
def fixture_projections(
    start_gameweek: int = Query(..., ge=1, le=38), end_gameweek: int = Query(..., ge=1, le=38)
) -> list[FixtureGameweekProjection]:
    try:
        return build_fixture_gameweek_projections(
            CurrentPlayerCatalogStore(data_dir()).latest_players(), CurrentFixtureCatalogStore(data_dir()).latest(),
            start_gameweek, end_gameweek,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/projections/xg-xa", response_model=list[XgXaProjection])
def xg_xa_projections(limit: int = Query(20, ge=1, le=1000)) -> list[XgXaProjection]:
    try:
        return XgXaProjectionStore(data_dir()).latest()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/odds/epl/latest", response_model=list[NormalizedMatchOdds])
def latest_epl_odds(limit: int = Query(20, ge=1, le=1000)) -> list[NormalizedMatchOdds]:
    try:
        return OddsSnapshotStore(data_dir()).latest_epl_h2h()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/odds/epl/fixture-consensus", response_model=list[FixtureOddsConsensus])
def latest_fixture_odds_consensus(limit: int = Query(20, ge=1, le=1000)) -> list[FixtureOddsConsensus]:
    try:
        return FixtureOddsConsensusStore(data_dir()).latest()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/projections/odds", response_model=list[OddsAdjustedGameweekProjection])
def odds_projections(
    start_gameweek: int = Query(..., ge=1, le=38), end_gameweek: int = Query(..., ge=1, le=38)
) -> list[OddsAdjustedGameweekProjection]:
    try:
        player_path, players = CurrentPlayerCatalogStore(data_dir()).latest_with_path()
        return build_odds_adjusted_projections(
            players, CurrentFixtureCatalogStore(data_dir()).latest(), FixtureOddsConsensusStore(data_dir()).latest(),
            start_gameweek, end_gameweek, elapsed_gameweeks(data_dir(), player_path, players),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/projection-catalogs/current", response_model=ProjectionCatalog, status_code=201)
def build_current_projection_catalog() -> ProjectionCatalog:
    try:
        return CurrentProjectionStore(data_dir()).build_latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projection-catalogs/xg-xa", response_model=XgXaProjectionCatalog, status_code=201)
def build_xg_xa_projection_catalog() -> XgXaProjectionCatalog:
    try:
        return XgXaProjectionStore(data_dir()).build_latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projection-catalogs/fixtures", response_model=FixtureProjectionCatalog, status_code=201)
def build_fixture_projection_catalog(request: GameweekRangeRequest) -> FixtureProjectionCatalog:
    try:
        return FixtureProjectionStore(data_dir()).build(request.start_gameweek, request.end_gameweek)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/projection-catalogs/fixtures/latest", response_model=list[FixtureGameweekProjection])
def latest_fixture_projection_catalog(limit: int = Query(20, ge=1, le=1000)) -> list[FixtureGameweekProjection]:
    try:
        return FixtureProjectionStore(data_dir()).latest()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projection-catalogs/odds", response_model=OddsProjectionCatalog, status_code=201)
def build_odds_projection_catalog(request: GameweekRangeRequest) -> OddsProjectionCatalog:
    try:
        return OddsProjectionStore(data_dir()).build(request.start_gameweek, request.end_gameweek)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/projection-catalogs/odds/latest", response_model=list[OddsAdjustedGameweekProjection])
def latest_odds_projection_catalog(limit: int = Query(20, ge=1, le=1000)) -> list[OddsAdjustedGameweekProjection]:
    try:
        return OddsProjectionStore(data_dir()).latest()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/odds/epl/snapshots", response_model=OddsSnapshotSummary, status_code=201)
def fetch_epl_odds() -> OddsSnapshotSummary:
    try:
        payload, headers = TheOddsApiClient.from_environment().fetch_epl_h2h()
        return OddsSnapshotStore(data_dir()).save_epl_h2h(payload, headers)
    except OddsSourceError as exc:
        status = 503 if "ODDS_API_KEY" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.post("/odds/epl/fixture-consensus", response_model=FixtureOddsConsensusCatalog, status_code=201)
def build_fixture_odds_consensus() -> FixtureOddsConsensusCatalog:
    try:
        return FixtureOddsConsensusStore(data_dir()).build_latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/transfers/hit-cost")
def calculate_transfer_hit_cost(request: TransferCostRequest) -> dict[str, int]:
    return {"hit_cost": transfer_hit_cost(request)}
