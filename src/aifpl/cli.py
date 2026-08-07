from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from aifpl.config import data_dir
from aifpl.current import CurrentPlayerCatalogStore
from aifpl.current_projections import CurrentProjectionStore
from aifpl.fixture_projections import FixtureProjectionStore
from aifpl.fixtures import CurrentFixtureCatalogStore
from aifpl.fpl import FplClient, FplSourceError
from aifpl.historical import HistoricalSeasonImporter, HistoricalSourceError
from aifpl.projections import BaselineBacktester
from aifpl.optimizer import SquadOptimizationError, optimize_squad
from aifpl.odds import OddsSnapshotStore, OddsSourceError, TheOddsApiClient
from aifpl.odds_matching import FixtureOddsConsensusStore
from aifpl.odds_projections import OddsProjectionStore
from aifpl.rules import SquadRequest, select_best_lineup, validate_squad as validate_fpl_squad
from aifpl.transfers import CurrentSquadState, plan_transfers as build_transfer_plan
from aifpl.xg_projections import XgXaProjectionStore
from aifpl.snapshots import SnapshotNotFoundError, SnapshotStore

app = typer.Typer(help="Tools for the AIFPL backend")


@app.command()
def fetch_bootstrap() -> None:
    """Download and save an immutable snapshot from the official FPL API."""
    try:
        payload = asyncio.run(FplClient().fetch_bootstrap())
        path, summary = SnapshotStore(data_dir()).save_bootstrap(payload)
    except FplSourceError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(f"Saved {path}")
    typer.echo(summary.model_dump_json())


@app.command()
def fetch_fixtures() -> None:
    """Download and save the official current-season fixture list."""
    try:
        payload = asyncio.run(FplClient().fetch_fixtures())
        path, summary = SnapshotStore(data_dir()).save_fixtures(payload)
    except FplSourceError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(f"Saved {path}")
    typer.echo(summary.model_dump_json())


@app.command()
def fetch_event(event: int = typer.Argument(..., min=1)) -> None:
    """Download and save player statistics for one FPL gameweek."""
    try:
        payload = asyncio.run(FplClient().fetch_event_live(event))
        path, summary = SnapshotStore(data_dir()).save_event_live(event, payload)
    except FplSourceError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(f"Saved {path}")
    typer.echo(summary.model_dump_json())


@app.command()
def latest_snapshot() -> None:
    """Show the most recently saved raw FPL snapshot."""
    try:
        path, summary = SnapshotStore(data_dir()).latest_bootstrap()
    except SnapshotNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(str(path))
    typer.echo(summary.model_dump_json())


@app.command()
def snapshot_before(at: str = typer.Argument(..., help="ISO-8601 cutoff such as 2026-08-07T19:00:00Z")) -> None:
    """Show the newest bootstrap snapshot captured no later than a cutoff."""
    try:
        cutoff = __import__("datetime").datetime.fromisoformat(at.replace("Z", "+00:00"))
        path, summary = SnapshotStore(data_dir()).bootstrap_before(cutoff)
    except (SnapshotNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(str(path))
    typer.echo(summary.model_dump_json())


@app.command()
def import_season(
    season: str = typer.Argument(..., help="Completed season, for example 2025-26"),
    start_gameweek: int = typer.Option(1, min=1, max=38),
    end_gameweek: int = typer.Option(38, min=1, max=38),
) -> None:
    """Import completed-season player results, retaining raw source files and hashes."""
    try:
        summary = HistoricalSeasonImporter(data_dir()).import_season(season, start_gameweek, end_gameweek)
    except (HistoricalSourceError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(summary))


def json_dumps(value: object) -> str:
    import json
    from dataclasses import asdict, is_dataclass
    from pydantic import BaseModel

    def default(item: object) -> object:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if is_dataclass(item):
            return asdict(item)
        return str(item)

    return json.dumps(value, default=default, sort_keys=True)


@app.command()
def backtest_baseline(
    season: str = typer.Argument(..., help="Previously imported season, for example 2025-26"),
    start_gameweek: int = typer.Option(..., min=1, max=38),
    end_gameweek: int = typer.Option(..., min=1, max=38),
    window: int = typer.Option(5, min=1, max=38),
    import_id: str | None = typer.Option(None, help="Historical import ID; defaults to the latest import"),
) -> None:
    """Evaluate a leakage-safe player rolling-average points baseline."""
    try:
        selected_import = import_id or HistoricalSeasonImporter(data_dir()).latest_import_id(season)
        summary = BaselineBacktester(data_dir()).run(season, selected_import, start_gameweek, end_gameweek, window)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(summary))


def load_squad(path: Path) -> SquadRequest:
    try:
        return SquadRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Could not load squad JSON: {exc}") from exc


@app.command()
def validate_squad(squad_file: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Validate an FPL squad JSON against the core composition, club, and budget rules."""
    typer.echo(json_dumps(validate_fpl_squad(load_squad(squad_file))))


@app.command()
def pick_lineup(squad_file: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Choose the highest-projected legal lineup, captain, vice, and bench."""
    try:
        recommendation = select_best_lineup(load_squad(squad_file))
    except ValueError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(recommendation))


@app.command()
def normalize_current_players() -> None:
    """Create a versioned real-player catalog from the latest FPL bootstrap snapshot."""
    try:
        catalog = CurrentPlayerCatalogStore(data_dir()).normalize_latest()
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))


@app.command()
def current_players(limit: int = typer.Option(20, min=1, max=1000)) -> None:
    """List real players from the most recently normalized current-season catalog."""
    try:
        players = CurrentPlayerCatalogStore(data_dir()).latest_players()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(players[:limit]))


@app.command()
def build_current_projections() -> None:
    """Build the transparent source-based baseline for real current-season players."""
    try:
        catalog = CurrentProjectionStore(data_dir()).build_latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))


@app.command()
def build_xg_xa_projections() -> None:
    """Build the versioned real-player FPL xG/xA comparison projection catalog."""
    try:
        catalog = XgXaProjectionStore(data_dir()).build_latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))


@app.command()
def xg_xa_projections(limit: int = typer.Option(20, min=1, max=1000)) -> None:
    """List the latest real-player xG/xA comparison projections."""
    try:
        projections = XgXaProjectionStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(projections[:limit]))


@app.command()
def fetch_epl_odds() -> None:
    """Fetch UK EPL match-result odds; reads ODDS_API_KEY only from the environment."""
    try:
        payload, headers = TheOddsApiClient.from_environment().fetch_epl_h2h()
        summary = OddsSnapshotStore(data_dir()).save_epl_h2h(payload, headers)
    except OddsSourceError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(summary))


@app.command()
def latest_epl_odds(limit: int = typer.Option(20, min=1, max=1000)) -> None:
    """List normalized implied EPL match probabilities from the newest odds snapshot."""
    try:
        odds = OddsSnapshotStore(data_dir()).latest_epl_h2h()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(odds[:limit]))


@app.command()
def build_fixture_odds_consensus() -> None:
    """Match FPL fixtures to odds events and build a margin-adjusted bookmaker consensus."""
    try:
        catalog = FixtureOddsConsensusStore(data_dir()).build_latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))


@app.command()
def fixture_odds_consensus(limit: int = typer.Option(20, min=1, max=1000)) -> None:
    """List the newest matched FPL fixture odds consensus."""
    try:
        rows = FixtureOddsConsensusStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(rows[:limit]))


@app.command()
def build_odds_projections(
    start_gameweek: int = typer.Option(..., min=1, max=38), end_gameweek: int = typer.Option(..., min=1, max=38)
) -> None:
    """Build a versioned xG/xA, fixture-difficulty, and odds-consensus projection catalog."""
    try:
        catalog = OddsProjectionStore(data_dir()).build(start_gameweek, end_gameweek)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))


@app.command()
def current_projections(limit: int = typer.Option(20, min=1, max=1000)) -> None:
    """List the latest real-player projection catalog."""
    try:
        projections = CurrentProjectionStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(projections[:limit]))


@app.command()
def optimize_current_squad(budget: int = typer.Option(1000, min=0)) -> None:
    """Choose the exact highest-projected legal squad from all current FPL players."""
    try:
        squad = optimize_squad(CurrentProjectionStore(data_dir()).latest(), budget)
    except (FileNotFoundError, SquadOptimizationError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(squad))


@app.command()
def plan_transfers(squad_file: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Compare hold and legal transfer plans for a current squad JSON state."""
    try:
        state = CurrentSquadState.model_validate(json.loads(squad_file.read_text(encoding="utf-8")))
        plan = build_transfer_plan(CurrentProjectionStore(data_dir()).latest(), state)
    except (OSError, ValueError, FileNotFoundError, SquadOptimizationError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(plan))


@app.command()
def normalize_current_fixtures() -> None:
    """Create a versioned current fixture catalog from the latest FPL fixture snapshot."""
    try:
        catalog = CurrentFixtureCatalogStore(data_dir()).normalize_latest()
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))


@app.command()
def build_fixture_projections(
    start_gameweek: int = typer.Option(..., min=1, max=38),
    end_gameweek: int = typer.Option(..., min=1, max=38),
) -> None:
    """Build a versioned fixture-aware projection catalog for a gameweek range."""
    try:
        catalog = FixtureProjectionStore(data_dir()).build(start_gameweek, end_gameweek)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))
