from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from aifpl.config import data_dir
from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.current_projections import CurrentPlayerProjection, CurrentProjectionStore
from aifpl.fixture_projections import FixtureGameweekProjection, build_fixture_gameweek_projections
from aifpl.fixtures import CurrentFixtureCatalogStore
from aifpl.historical import HistoricalSeasonImporter, SeasonImportSummary
from aifpl.optimizer import OptimizedSquad, SquadOptimizationError, optimize_squad
from aifpl.odds import NormalizedMatchOdds, OddsSnapshotStore
from aifpl.odds_matching import FixtureOddsConsensus, FixtureOddsConsensusStore
from aifpl.odds_projections import OddsAdjustedGameweekProjection, OddsProjectionStore, build_odds_adjusted_projections
from aifpl.rules import LineupRecommendation, SquadRequest, SquadValidation, select_best_lineup, validate_squad
from aifpl.transfers import CurrentSquadState, TransferPlan, plan_transfers
from aifpl.xg_projections import XgXaProjection, XgXaProjectionStore
from aifpl.snapshots import SnapshotNotFoundError, SnapshotStore

app = FastAPI(title="AIFPL Backend", version="0.1.0")


class LatestSnapshotResponse(BaseModel):
    path: str
    summary: dict[str, object]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/snapshots/latest", response_model=LatestSnapshotResponse)
def latest_snapshot() -> LatestSnapshotResponse:
    try:
        path, summary = SnapshotStore(data_dir()).latest_bootstrap()
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return LatestSnapshotResponse(path=str(path), summary=summary.model_dump(mode="json"))


@app.get("/snapshots/as-of", response_model=LatestSnapshotResponse)
def snapshot_as_of(at: datetime = Query(description="Timezone-aware ISO-8601 cutoff")) -> LatestSnapshotResponse:
    try:
        path, summary = SnapshotStore(data_dir()).bootstrap_before(at)
    except (SnapshotNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return LatestSnapshotResponse(path=str(path), summary=summary.model_dump(mode="json"))


@app.get("/historical/seasons/{season}", response_model=SeasonImportSummary)
def historical_season_summary(season: str) -> SeasonImportSummary:
    try:
        return HistoricalSeasonImporter(data_dir()).summary(season)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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


@app.get("/projections/current", response_model=list[CurrentPlayerProjection])
def current_projections(limit: int = Query(20, ge=1, le=1000)) -> list[CurrentPlayerProjection]:
    try:
        return CurrentProjectionStore(data_dir()).latest()[:limit]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/squad/optimize/current", response_model=OptimizedSquad)
def optimize_current_squad(budget: int = Query(1000, ge=0)) -> OptimizedSquad:
    try:
        return optimize_squad(CurrentProjectionStore(data_dir()).latest(), budget)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (SquadOptimizationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/transfers/plan", response_model=TransferPlan)
def transfer_plan(state: CurrentSquadState) -> TransferPlan:
    try:
        return plan_transfers(CurrentProjectionStore(data_dir()).latest(), state)
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
        return build_odds_adjusted_projections(
            CurrentPlayerCatalogStore(data_dir()).latest_players(), CurrentFixtureCatalogStore(data_dir()).latest(),
            FixtureOddsConsensusStore(data_dir()).latest(), start_gameweek, end_gameweek,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
