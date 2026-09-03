from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from threading import Event, Thread

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from aifpl.config import cors_origins, data_dir
from aifpl.calibration import CalibrationReport, ErrorMetrics, compare_prediction_runs, fit_walk_forward_calibration
from aifpl.account import (
    AccountSnapshot,
    AccountSnapshotStore,
    fetch_and_build_account_state,
    latest_internal_squad_context,
)
from aifpl.chips import ChipAdvice, ChipAdviceStore, ChipState, ChipStateStore
from aifpl.captaincy_strategy import choose_captain
from aifpl.current import CurrentPlayer, CurrentPlayerCatalog, CurrentPlayerCatalogStore
from aifpl.current_projections import CurrentPlayerProjection, CurrentProjectionStore, ProjectionCatalog
from aifpl.dashboard import CurrentDashboard, build_current_dashboard
from aifpl.execution import ExecutionConfirmation, ExecutionConfirmationError, ExecutionConfirmationStore
from aifpl.game_state import ExposureState, GameState, GameStateStore, ObjectiveMode
from aifpl.fixture_projections import FixtureGameweekProjection, FixtureProjectionCatalog, FixtureProjectionStore, build_fixture_gameweek_projections
from aifpl.fixtures import CurrentFixture, CurrentFixtureCatalogStore, FixtureCatalog
from aifpl.fpl import FplClient, FplSourceError
from aifpl.historical import HistoricalSeasonImporter, HistoricalSourceError, SeasonImportSummary
from aifpl.health import SourceHealthChecker, SourceHealthReport
from aifpl.hermes import (
    HermesDecision, HermesManager, HermesRunResult, HermesRunTranscript,
    HermesState, HermesSupersessionResult,
)
from aifpl.horizon_transfers import HorizonSquadState, HorizonTransferPlan, plan_horizon_transfers
from aifpl.live_calibration import calibrated_odds_rows
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
from aifpl.strategy_policy import StrategyPolicy, derive_strategy_policy
from aifpl.template import (
    OwnershipLandscape,
    PlayerTemplateState,
    TemplateCatalog,
    TemplateCatalogStore,
    build_template_catalog,
    build_exposure_states,
)
from aifpl.xg_projections import XgXaProjection, XgXaProjectionCatalog, XgXaProjectionStore, elapsed_gameweeks
from aifpl.snapshots import SnapshotNotFoundError, SnapshotStore
from aifpl.security import valid_admin_key
from aifpl.tavily_news import TavilyNewsStore
from aifpl.teams import CurrentTeam, CurrentTeamCatalogStore, team_logo_path


def _scheduler_enabled() -> bool:
    return os.environ.get("AIFPL_SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes")


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop_event = Event()
    scheduler_thread: Thread | None = None
    if _scheduler_enabled():
        scheduler_thread = Thread(
            target=DeadlineScheduler(data_dir()).run_forever,
            args=(stop_event,),
            daemon=True,
            name="aifpl-deadline-scheduler",
        )
        scheduler_thread.start()
    try:
        yield
    finally:
        stop_event.set()
        if scheduler_thread is not None:
            scheduler_thread.join(timeout=5)


app = FastAPI(title="AIFPL Backend", version="0.1.0", lifespan=lifespan)
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


def _protect_sensitive_read(request: Request) -> None:
    """Debug output and model transcripts are private unless explicitly exposed."""
    if os.environ.get("AIFPL_ALLOW_ANONYMOUS_SENSITIVE_READS", "false").lower() in ("1", "true", "yes"):
        return
    if not os.environ.get("AIFPL_ADMIN_API_KEY"):
        raise HTTPException(status_code=503, detail="AIFPL_ADMIN_API_KEY is not configured")
    if not valid_admin_key(request.headers.get("X-AIFPL-Admin-Key")):
        raise HTTPException(status_code=401, detail="Invalid or missing admin API key")


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


class BacktestRunSummary(BaseModel):
    season: str
    run: str
    created_at: datetime
    predictions_path: str
    record_count: int
    parameters: dict[str, object] = Field(default_factory=dict)
    metrics: dict[str, object] = Field(default_factory=dict)
    comparable: bool = False


class HermesMigrationRequest(BaseModel):
    purchase_prices: dict[int, int]
    gameweek: int = Field(ge=1, le=38)
    season_id: str


class HermesSupersedeRequest(BaseModel):
    base_state_id: str = Field(min_length=1, max_length=255)
    supersedes_decision_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=1000)
    active_chip: str | None = Field(default=None, pattern=r"^(wildcard|free_hit|bench_boost|triple_captain)$")
    active_chip_set: int | None = Field(default=None, ge=1, le=2)


class HermesReplanRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    active_chip: str | None = Field(default=None, pattern=r"^(wildcard|free_hit|bench_boost|triple_captain)$")
    active_chip_set: int | None = Field(default=None, ge=1, le=2)


class ChipStateRequest(BaseModel):
    season_id: str
    chip: str
    chip_set: int = Field(ge=1, le=2)
    gameweek: int = Field(ge=1, le=38)


class AccountStateRequest(BaseModel):
    entry_id: int = Field(gt=0)
    season_id: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
    target_rank: int = Field(gt=0)
    free_transfers: int | None = Field(default=None, ge=0, le=5)
    initial_free_transfers: int = Field(default=0, ge=0, le=5)
    gameweek: int | None = Field(default=None, ge=1, le=38)
    decision_gameweek: int | None = Field(default=None, ge=1, le=38)
    chips_remaining: dict[str, int] | None = None


class ExecutionConfirmationRequest(BaseModel):
    decision_path: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="manual", pattern="^(manual|fpl_import)$")
    squad_ids: list[int] = Field(min_length=15, max_length=15)
    starting_xi_ids: list[int] = Field(min_length=11, max_length=11)
    bench_ids: list[int] = Field(min_length=4, max_length=4)
    captain_id: int
    vice_captain_id: int
    transfers_out: list[int] | None = None
    transfers_in: list[int] | None = None
    hit_cost: int = Field(default=0, ge=0)
    free_transfers_before: int | None = Field(default=None, ge=0, le=5)
    pre_execution_squad_ids: list[int] | None = None
    active_chip: str | None = None
    active_chip_set: int | None = Field(default=None, ge=1, le=2)
    pre_free_hit_squad_ids: list[int] | None = None
    pre_free_hit_bank: int | None = Field(default=None, ge=0)
    pre_free_hit_free_transfers: int | None = Field(default=None, ge=0, le=5)
    pre_free_hit_purchase_prices: dict[int, int] | None = None
    confirmed_at: datetime | None = None
    notes: str = Field(default="", max_length=2000)


class CaptaincyRequest(BaseModel):
    players: list[CurrentPlayerProjection] = Field(min_length=2, max_length=11)
    triple_captain: bool = False


def _rank_context(
    objective_mode: ObjectiveMode,
) -> tuple[GameState | None, dict[int, PlayerTemplateState]]:
    if objective_mode == "POINTS_MODE":
        return None, {}
    try:
        state = GameStateStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail="RANK_MODE requires a saved GameState") from exc
    if not state.rank_data_available:
        raise HTTPException(status_code=422, detail="RANK_MODE requires a GameState with rank and target rank")
    try:
        templates = {
            row.player_id: row
            for row in TemplateCatalogStore(data_dir()).latest(
                season_id=state.season_id, gameweek=state.gameweek,
            ).players
        }
    except FileNotFoundError:
        try:
            templates = {
                row.player_id: row
                for row in TemplateCatalogStore(data_dir()).latest(season_id=state.season_id).players
            }
        except FileNotFoundError:
            templates = {}
    return state.model_copy(update={"objective_mode": "RANK_MODE"}), templates


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/game-state", response_model=GameState)
def latest_game_state(request: Request) -> GameState:
    _protect_sensitive_read(request)
    try:
        return GameStateStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/game-state", response_model=GameState, status_code=201)
def save_game_state(state: GameState) -> GameState:
    GameStateStore(data_dir()).save(state)
    return state


@app.post("/game-state/account", response_model=GameState, status_code=201)
def import_account_game_state(request: AccountStateRequest) -> GameState:
    try:
        internal_squad_ids, internal_gameweek, reconciliation_source = latest_internal_squad_context(
            data_dir(), request.season_id,
        )
        snapshot, state = asyncio.run(fetch_and_build_account_state(
            FplClient(),
            entry_id=request.entry_id,
            season_id=request.season_id,
            target_rank=request.target_rank,
            free_transfers=request.free_transfers,
            initial_free_transfers=request.initial_free_transfers,
            chips_remaining=request.chips_remaining,
            gameweek=request.gameweek,
            decision_gameweek=request.decision_gameweek,
            internal_squad_ids=internal_squad_ids,
            internal_gameweek=internal_gameweek,
            reconciliation_source=reconciliation_source,
        ))
        AccountSnapshotStore(data_dir()).save(snapshot)
        GameStateStore(data_dir()).save(state)
        return state
    except FplSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/account/latest", response_model=AccountSnapshot)
def latest_account_snapshot(
    request: Request,
    entry_id: int | None = Query(None, ge=1),
    season_id: str | None = Query(None, min_length=3, max_length=32),
) -> AccountSnapshot:
    _protect_sensitive_read(request)
    try:
        return AccountSnapshotStore(data_dir()).latest(entry_id=entry_id, season_id=season_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/template/landscape", response_model=TemplateCatalog)
def latest_template_landscape(
    request: Request,
    season_id: str | None = Query(None, min_length=3, max_length=32),
    gameweek: int | None = Query(None, ge=1, le=38),
) -> TemplateCatalog:
    _protect_sensitive_read(request)
    try:
        return TemplateCatalogStore(data_dir()).latest(season_id, gameweek)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/template/players", response_model=list[PlayerTemplateState])
def latest_template_players(
    request: Request,
    limit: int = Query(1000, ge=1, le=5000),
    season_id: str | None = Query(None, min_length=3, max_length=32),
    gameweek: int | None = Query(None, ge=1, le=38),
) -> list[PlayerTemplateState]:
    _protect_sensitive_read(request)
    try:
        return TemplateCatalogStore(data_dir()).latest(season_id, gameweek).players[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/template/landscape", response_model=TemplateCatalog, status_code=201)
def save_template_landscape(landscape: OwnershipLandscape) -> TemplateCatalog:
    catalog = build_template_catalog(landscape)
    path = TemplateCatalogStore(data_dir()).save(catalog)
    return catalog.model_copy(update={"output_path": str(path)})


@app.get("/game-state/exposure", response_model=list[ExposureState])
def latest_exposure(request: Request) -> list[ExposureState]:
    _protect_sensitive_read(request)
    try:
        state = GameStateStore(data_dir()).latest()
        if state.exposures:
            return state.exposures
        hermes_state = HermesManager(data_dir()).latest_state(
            optional=True, season_id=state.season_id,
        )
        if hermes_state is None:
            return []
        try:
            rows = OddsProjectionStore(data_dir()).latest()
        except FileNotFoundError:
            return []
        try:
            templates = {
                row.player_id: row
                for row in TemplateCatalogStore(data_dir()).latest(
                    season_id=state.season_id, gameweek=state.gameweek,
                ).players
            }
        except FileNotFoundError:
            templates = {}
        return build_exposure_states(
            [row for row in rows if row.gameweek == state.gameweek],
            set(hermes_state.squad.player_ids),
            hermes_state.captain_id,
            hermes_state.active_chip == "triple_captain",
            templates,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/strategy/policy", response_model=StrategyPolicy)
def strategy_policy(
    request: Request,
    objective_mode: ObjectiveMode = Query("POINTS_MODE"),
) -> StrategyPolicy:
    _protect_sensitive_read(request)
    state, _ = _rank_context(objective_mode)
    if state is None:
        state = GameState(season_id="unknown", gameweek=1, free_transfers=1, bank=0)
    return derive_strategy_policy(state, objective_mode)


@app.post("/captaincy/plan")
def captaincy_plan(
    request: CaptaincyRequest,
    objective_mode: ObjectiveMode = Query("POINTS_MODE"),
) -> dict[str, object]:
    try:
        game_state, templates = _rank_context(objective_mode)
        choice = choose_captain(request.players, game_state, templates, request.triple_captain)
    except (HTTPException, ValueError) as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "mode": choice.mode,
        "captain_id": choice.captain.player_id,
        "vice_captain_id": choice.vice_captain.player_id,
        "options": [
            {
                "player_id": option.player_id,
                "projected_points": option.projected_points,
                "target_cohort_eo": option.target_cohort_eo,
                "own_exposure_if_captained": option.own_exposure_if_captained,
                "net_exposure": option.net_exposure,
                "score": option.score,
                "classification": option.classification,
                "rank_swing_potential": option.rank_swing_potential,
                "ownership_basis": option.ownership_basis,
                "ownership_confidence": option.ownership_confidence,
            }
            for option in choice.options
        ],
    }


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
def run_hermes(
    objective_mode: ObjectiveMode | None = Query(None),
) -> HermesRunResult:
    try:
        return HermesManager(data_dir()).run_current(objective_mode=objective_mode)
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
def latest_hermes_decision(
    season_id: str | None = Query(None, min_length=1, max_length=32),
    gameweek: int | None = Query(None, ge=1, le=38),
) -> HermesDecision:
    try:
        manager = HermesManager(data_dir())
        if season_id is None and gameweek is None:
            return manager.latest_decision()
        return manager.latest_decision(season_id=season_id, gameweek=gameweek)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/hermes/decisions", response_model=list[HermesDecision])
def hermes_decision_history(
    limit: int = Query(50, ge=1, le=200),
    season_id: str | None = Query(None, min_length=1, max_length=32),
    gameweek: int | None = Query(None, ge=1, le=38),
) -> list[HermesDecision]:
    try:
        manager = HermesManager(data_dir())
        if season_id is None and gameweek is None:
            return manager.decisions(limit)
        return manager.decisions(limit, season_id=season_id, gameweek=gameweek)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/execution/confirmations", response_model=ExecutionConfirmation, status_code=201)
def confirm_execution(request: ExecutionConfirmationRequest) -> ExecutionConfirmation:
    try:
        return ExecutionConfirmationStore(data_dir()).confirm(
            request.decision_path,
            source=request.source,
            squad_ids=request.squad_ids,
            starting_xi_ids=request.starting_xi_ids,
            bench_ids=request.bench_ids,
            captain_id=request.captain_id,
            vice_captain_id=request.vice_captain_id,
            transfers_out=request.transfers_out,
            transfers_in=request.transfers_in,
            hit_cost=request.hit_cost,
            free_transfers_before=request.free_transfers_before,
            pre_execution_squad_ids=request.pre_execution_squad_ids,
            active_chip=request.active_chip,
            active_chip_set=request.active_chip_set,
            pre_free_hit_squad_ids=request.pre_free_hit_squad_ids,
            pre_free_hit_bank=request.pre_free_hit_bank,
            pre_free_hit_free_transfers=request.pre_free_hit_free_transfers,
            pre_free_hit_purchase_prices=request.pre_free_hit_purchase_prices,
            confirmed_at=request.confirmed_at,
            notes=request.notes,
        )
    except ExecutionConfirmationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/execution/confirmations/latest", response_model=ExecutionConfirmation)
def latest_execution_confirmation(
    request: Request,
    season_id: str = Query(..., min_length=1, max_length=32),
    gameweek: int = Query(..., ge=1, le=38),
) -> ExecutionConfirmation:
    _protect_sensitive_read(request)
    confirmation = ExecutionConfirmationStore(data_dir()).latest(season_id, gameweek)
    if confirmation is None:
        raise HTTPException(status_code=404, detail=f"No execution confirmation exists for {season_id} GW{gameweek}")
    return confirmation


@app.get("/hermes/runs/latest", response_model=HermesRunTranscript)
def latest_hermes_run_transcript(
    request: Request,
    season_id: str | None = Query(None, min_length=1, max_length=32),
    gameweek: int | None = Query(None, ge=1, le=38),
) -> HermesRunTranscript:
    _protect_sensitive_read(request)
    try:
        manager = HermesManager(data_dir())
        if season_id is None and gameweek is None:
            return manager.latest_transcript()
        return manager.latest_transcript(season_id=season_id, gameweek=gameweek)
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


@app.post("/hermes/decisions/supersede", response_model=HermesSupersessionResult, status_code=201)
def supersede_hermes_decision(request: HermesSupersedeRequest) -> HermesSupersessionResult:
    try:
        root = data_dir()
        schedule = DeadlineScheduler(root).status()
        if schedule.missed:
            raise ValueError(f"GW{schedule.event} has already passed its deadline")
        kwargs: dict[str, object] = {}
        if request.active_chip is not None or request.active_chip_set is not None:
            kwargs = {
                "active_chip": request.active_chip,
                "active_chip_set": request.active_chip_set,
            }
        return HermesManager(root).supersede_decision(
            request.base_state_id,
            request.supersedes_decision_id,
            request.reason,
            schedule.event,
            schedule.season_id,
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/hermes/replan-current", response_model=HermesSupersessionResult, status_code=201)
def replan_current_hermes(request: HermesReplanRequest) -> HermesSupersessionResult:
    try:
        return HermesManager(data_dir()).replan_current(
            request.reason,
            active_chip=request.active_chip,
            active_chip_set=request.active_chip_set,
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


@app.get("/calibration/backtests", response_model=list[BacktestRunSummary])
def list_backtest_runs() -> list[BacktestRunSummary]:
    root = data_dir()
    runs: list[BacktestRunSummary] = []
    for manifest in sorted((root / "backtests").glob("*/*/backtest.manifest.json"), reverse=True):
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        parameters = document.get("parameters", {})
        metrics = document.get("metrics", {})
        try:
            created_at = datetime.fromisoformat(str(document.get("created_at", "")))
        except ValueError:
            continue
        runs.append(BacktestRunSummary(
            season=manifest.parent.parent.name,
            run=manifest.parent.name,
            created_at=created_at,
            predictions_path=str(document.get("artifact_path", "")),
            record_count=int(metrics.get("predictions", 0)),
            parameters=parameters,
            metrics=metrics,
        ))
    counts: dict[str, int] = defaultdict(int)
    for run in runs:
        counts[run.season] += 1
    return [run.model_copy(update={"comparable": counts[run.season] >= 2}) for run in runs]


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


@app.get("/news/tavily/latest")
def latest_tavily_news() -> dict[str, object]:
    try:
        return TavilyNewsStore(data_dir()).latest_payload()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/chips/advice/latest", response_model=ChipAdvice)
def latest_chip_advice() -> ChipAdvice:
    try:
        return ChipAdviceStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/chips/state", response_model=ChipState, status_code=201)
def mark_chip_used(request: ChipStateRequest) -> ChipState:
    try:
        return ChipStateStore(data_dir()).mark_used(
            request.season_id, request.chip, request.chip_set, request.gameweek,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/odds/epl/event-markets", response_model=EventMarketCatalog, status_code=201)
def fetch_event_markets() -> EventMarketCatalog:
    try:
        schedule = DeadlineScheduler(data_dir()).status()
        consensus = FixtureOddsConsensusStore(data_dir()).latest()
        event_ids = [row.odds_event_id for row in consensus if row.gameweek == schedule.event]
        if not event_ids:
            event_ids = [row.odds_event_id for row in consensus]
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


@app.get("/scheduler/ticks", response_model=list[SchedulerTickResult])
def scheduler_ticks(limit: int = Query(20, ge=1, le=200)) -> list[SchedulerTickResult]:
    directory = data_dir() / "scheduler" / "ticks"
    files = sorted(directory.glob("*.json"), reverse=True) if directory.exists() else []
    ticks: list[SchedulerTickResult] = []
    for path in files:
        try:
            ticks.append(SchedulerTickResult.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    updates = {
        tick.notification_update_for: tick
        for tick in ticks
        if tick.notification_update_for is not None
    }
    return [
        updates.get(tick.output_path, tick)
        for tick in ticks
        if tick.notification_update_for is None
    ][:limit]


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


@app.get("/debug/bootstrap-log")
def bootstrap_log(request: Request) -> dict[str, object]:
    _protect_sensitive_read(request)
    log_path = Path("/tmp/aifpl-bootstrap.log")
    if not log_path.is_file():
        return {"exists": False, "log": ""}
    return {"exists": True, "log": log_path.read_text(encoding="utf-8", errors="replace")[-8000:]}


@app.get("/debug/scheduler-log")
def scheduler_log(request: Request) -> dict[str, object]:
    _protect_sensitive_read(request)
    log_path = Path("/tmp/aifpl-scheduler.log")
    if not log_path.is_file():
        return {"exists": False, "log": ""}
    return {"exists": True, "log": log_path.read_text(encoding="utf-8", errors="replace")[-8000:]}


@app.get("/debug/env")
def debug_env(request: Request) -> dict[str, object]:
    _protect_sensitive_read(request)
    keys = (
        "AIFPL_DATA_DIR", "AIFPL_CORS_ORIGINS", "AIFPL_RENDER_BOOTSTRAP",
        "AIFPL_HERMES_AUTO_RUN", "AIFPL_ACCOUNT_AUTO_SYNC", "AIFPL_ACCOUNT_ENTRY_ID",
        "AIFPL_ACCOUNT_TARGET_RANK", "ODDS_API_KEY", "HERMES_API_KEY", "HERMES_MODEL",
    )
    return {
        "present": {key: bool(os.environ.get(key)) for key in keys},
        "data_dir": str(data_dir()),
    }


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
    objective_mode: ObjectiveMode = Query("POINTS_MODE"),
) -> OptimizedSquad:
    try:
        game_state, templates = _rank_context(objective_mode)
        return optimize_squad(
            load_projection_candidates(data_dir(), projection_source, catalog_id), budget,
            differential_appetite=differential_appetite,
            objective_mode=objective_mode, game_state=game_state, template_states=templates,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (SquadOptimizationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/transfers/plan", response_model=TransferPlan)
def transfer_plan(
    state: CurrentSquadState, projection_source: ProjectionSource = Query(ProjectionSource.CURRENT),
    catalog_id: str | None = Query(None),
    objective_mode: ObjectiveMode = Query("POINTS_MODE"),
) -> TransferPlan:
    try:
        game_state, templates = _rank_context(objective_mode)
        return plan_transfers(
            load_projection_candidates(data_dir(), projection_source, catalog_id), state,
            objective_mode=objective_mode, game_state=game_state, template_states=templates,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (SquadOptimizationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/transfers/plan/horizon", response_model=HorizonTransferPlan)
def horizon_transfer_plan(
    state: HorizonSquadState,
    catalog_id: str | None = Query(None),
    pre_season: bool = Query(False, description="Unlimited transfers for the opening gameweek only"),
    decision_hit_penalty: float = Query(4.0, ge=0),
    churn_penalty: float | None = Query(None, ge=0),
    objective_mode: ObjectiveMode = Query("POINTS_MODE"),
    active_chip: str | None = Query(
        None, pattern=r"^(wildcard|free_hit|bench_boost|triple_captain)$",
        description="Optional chip active in the first horizon gameweek.",
    ),
) -> HorizonTransferPlan:
    try:
        game_state, templates = _rank_context(objective_mode)
        return plan_horizon_transfers(
            calibrated_odds_rows(data_dir(), catalog_id)[0], state,
            decision_hit_penalty=decision_hit_penalty, pre_season=pre_season,
            churn_penalty=churn_penalty, objective_mode=objective_mode,
            game_state=game_state, template_states=templates,
            active_chip=active_chip,
        )
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
        try:
            template_states = {
                row.player_id: row for row in TemplateCatalogStore(data_dir()).latest().players
            }
        except FileNotFoundError:
            template_states = {}
        return build_odds_adjusted_projections(
            players, CurrentFixtureCatalogStore(data_dir()).latest(), FixtureOddsConsensusStore(data_dir()).latest(),
            start_gameweek, end_gameweek, elapsed_gameweeks(data_dir(), player_path, players),
            template_states=template_states,
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
