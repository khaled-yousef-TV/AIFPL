from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import typer

from aifpl.account import (
    AccountSnapshotStore,
    build_account_snapshot,
    fetch_and_build_account_state,
    latest_internal_squad_context,
)
from aifpl.config import account_sync_settings, data_dir
from aifpl.calibration import compare_prediction_runs, fit_walk_forward_calibration
from aifpl.chips import ChipAdviceStore, ChipStateStore
from aifpl.captaincy_strategy import choose_captain
from aifpl.current import CurrentPlayerCatalogStore
from aifpl.current_projections import CurrentPlayerProjection, CurrentProjectionStore
from aifpl.fixture_projections import FixtureProjectionStore
from aifpl.fixtures import CurrentFixtureCatalogStore
from aifpl.fpl import FplClient, FplSourceError
from aifpl.game_state import GameState, GameStateStore, ObjectiveMode
from aifpl.historical import HistoricalSeasonImporter, HistoricalSourceError
from aifpl.health import SourceHealthChecker
from aifpl.hermes import HermesManager
from aifpl.horizon_transfers import HorizonSquadState, plan_horizon_transfers
from aifpl.live_calibration import calibrated_odds_rows
from aifpl.projections import BaselineBacktester
from aifpl.projection_catalogs import ProjectionSource, load_projection_candidates
from aifpl.player_evidence import PlayerEvidenceStore
from aifpl.market_odds import EventMarketStore
from aifpl.market_signals import MarketSignalStore
from aifpl.refresh import CurrentDataRefreshJob, RefreshJobError
from aifpl.scheduler import DeadlineScheduler, SchedulerTickError
from aifpl.optimizer import SquadOptimizationError, optimize_squad
from aifpl.odds import OddsSnapshotStore, OddsSourceError, TheOddsApiClient
from aifpl.odds_matching import FixtureOddsConsensusStore, load_team_aliases
from aifpl.odds_projections import OddsProjectionStore
from aifpl.rules import SquadRequest, select_best_lineup, validate_squad as validate_fpl_squad
from aifpl.strategy_policy import derive_strategy_policy
from aifpl.template import OwnershipLandscape, PlayerTemplateState, TemplateCatalogStore, build_template_catalog
from aifpl.tavily_news import TavilyNewsStore
from aifpl.transfers import CurrentSquadState, plan_transfers as build_transfer_plan
from aifpl.xg_projections import XgXaProjectionStore
from aifpl.snapshots import SnapshotNotFoundError, SnapshotStore

app = typer.Typer(help="Tools for the AIFPL backend")


def _rank_context(objective_mode: ObjectiveMode) -> tuple[GameState | None, dict[int, PlayerTemplateState]]:
    if objective_mode == "POINTS_MODE":
        return None, {}
    try:
        state = GameStateStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise ValueError("RANK_MODE requires a saved GameState") from exc
    if not state.rank_data_available:
        raise ValueError("RANK_MODE requires a GameState with rank and target rank")
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


@app.command()
def save_game_state(state_file: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Persist a rank/account GameState JSON document."""
    try:
        state = GameState.model_validate_json(state_file.read_text(encoding="utf-8"))
        path = GameStateStore(data_dir()).save(state)
    except (OSError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps({"output_path": str(path), "state": state}))


@app.command()
def latest_game_state() -> None:
    """Show the latest persisted rank/account GameState."""
    try:
        state = GameStateStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(state))


@app.command()
def import_account_state(
    history_file: Path = typer.Argument(..., exists=True, readable=True),
    picks_file: Path = typer.Argument(..., exists=True, readable=True),
    entry_id: int = typer.Option(..., min=1),
    season_id: str = typer.Option(...),
    target_rank: int = typer.Option(..., min=1),
    free_transfers: int = typer.Option(..., min=0, max=5),
    gameweek: int | None = typer.Option(None, min=1, max=38),
    decision_gameweek: int | None = typer.Option(None, min=1, max=38),
    chips_file: Path | None = typer.Option(None, exists=True, readable=True),
) -> None:
    """Import explicit public FPL account history and picks into rank state."""
    try:
        history = json.loads(history_file.read_text(encoding="utf-8"))
        picks = json.loads(picks_file.read_text(encoding="utf-8"))
        chips = json.loads(chips_file.read_text(encoding="utf-8")) if chips_file is not None else None
        snapshot, state = build_account_snapshot(
            history,
            picks,
            entry_id=entry_id,
            season_id=season_id,
            target_rank=target_rank,
            free_transfers=free_transfers,
            chips_remaining=chips,
            gameweek=gameweek,
            decision_gameweek=decision_gameweek,
        )
        account_path = AccountSnapshotStore(data_dir()).save(snapshot)
        state_path = GameStateStore(data_dir()).save(state)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps({"account_path": str(account_path), "state_path": str(state_path), "state": state}))


@app.command()
def fetch_account_state(
    entry_id: int = typer.Argument(..., min=1),
    season_id: str = typer.Option(...),
    target_rank: int = typer.Option(..., min=1),
    free_transfers: int | None = typer.Option(None, min=0, max=5),
    initial_free_transfers: int = typer.Option(0, min=0, max=5),
    gameweek: int | None = typer.Option(None, min=1, max=38),
    decision_gameweek: int | None = typer.Option(None, min=1, max=38),
    chips_file: Path | None = typer.Option(None, exists=True, readable=True),
) -> None:
    """Fetch public FPL account history and picks, then persist rank state."""
    try:
        client = FplClient()
        chips = json.loads(chips_file.read_text(encoding="utf-8")) if chips_file is not None else None
        internal_squad_ids, internal_gameweek, reconciliation_source = latest_internal_squad_context(
            data_dir(), season_id,
        )
        snapshot, state = asyncio.run(fetch_and_build_account_state(
            client,
            entry_id=entry_id,
            season_id=season_id,
            target_rank=target_rank,
            free_transfers=free_transfers,
            initial_free_transfers=initial_free_transfers,
            chips_remaining=chips,
            gameweek=gameweek,
            decision_gameweek=decision_gameweek,
            internal_squad_ids=internal_squad_ids,
            internal_gameweek=internal_gameweek,
            reconciliation_source=reconciliation_source,
        ))
        account_path = AccountSnapshotStore(data_dir()).save(snapshot)
        state_path = GameStateStore(data_dir()).save(state)
    except (FplSourceError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps({"account_path": str(account_path), "state_path": str(state_path), "state": state}))


@app.command()
def sync_account_state() -> None:
    """Automatically refresh the configured public FPL account rank state."""
    try:
        settings = account_sync_settings()
        if not settings.enabled or settings.entry_id is None or settings.target_rank is None:
            raise ValueError("Account auto-sync is not configured")
        schedule = DeadlineScheduler(data_dir()).status()
        internal_squad_ids, internal_gameweek, reconciliation_source = latest_internal_squad_context(
            data_dir(), schedule.season_id,
        )
        snapshot, state = asyncio.run(fetch_and_build_account_state(
            FplClient(),
            entry_id=settings.entry_id,
            season_id=schedule.season_id,
            target_rank=settings.target_rank,
            initial_free_transfers=settings.initial_free_transfers,
            decision_gameweek=schedule.event,
            internal_squad_ids=internal_squad_ids,
            internal_gameweek=internal_gameweek,
            reconciliation_source=reconciliation_source,
        ))
        account_path = AccountSnapshotStore(data_dir()).save(snapshot)
        state_path = GameStateStore(data_dir()).save(state)
    except (FplSourceError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps({"account_path": str(account_path), "state_path": str(state_path), "state": state}))


@app.command()
def latest_account_state(
    entry_id: int | None = typer.Option(None, min=1),
    season_id: str | None = typer.Option(None),
) -> None:
    """Show the latest imported public FPL account snapshot."""
    try:
        snapshot = AccountSnapshotStore(data_dir()).latest(entry_id=entry_id, season_id=season_id)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(snapshot))


@app.command()
def build_template(ownership_file: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Build and persist a template catalog from an ownership landscape JSON document."""
    try:
        landscape = OwnershipLandscape.model_validate_json(ownership_file.read_text(encoding="utf-8"))
        catalog = build_template_catalog(landscape)
        path = TemplateCatalogStore(data_dir()).save(catalog)
    except (OSError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog.model_copy(update={"output_path": str(path)})))


@app.command()
def plan_captaincy(
    projections_file: Path = typer.Argument(..., exists=True, readable=True),
    objective_mode: ObjectiveMode = typer.Option("POINTS_MODE"),
) -> None:
    """Rank captain and vice-captain options from a projection JSON list."""
    try:
        from pydantic import TypeAdapter

        players = TypeAdapter(list[CurrentPlayerProjection]).validate_json(
            projections_file.read_text(encoding="utf-8"),
        )
        game_state, templates = _rank_context(objective_mode)
        choice = choose_captain(players, game_state, templates)
    except (OSError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(choice))


@app.command()
def calibrate_backtest(
    predictions_file: Path = typer.Argument(..., exists=True, readable=True),
    train_end_gameweek: int = typer.Option(..., min=1, max=38),
    evaluation_start_gameweek: int = typer.Option(..., min=1, max=38),
) -> None:
    """Fit on earlier archived predictions and evaluate calibration on later gameweeks."""
    try:
        if not predictions_file.resolve().is_relative_to(data_dir().resolve()):
            raise ValueError("predictions_file must be below AIFPL_DATA_DIR")
        report = fit_walk_forward_calibration(
            data_dir(), predictions_file, train_end_gameweek, evaluation_start_gameweek,
        )
    except (OSError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(report))


@app.command()
def compare_backtests(prediction_files: list[Path] = typer.Argument(..., exists=True, readable=True)) -> None:
    """Compare archived prediction runs on their common player-gameweek population."""
    try:
        comparison = compare_prediction_runs(data_dir(), prediction_files)
    except (OSError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(comparison))


@app.command()
def build_player_evidence() -> None:
    """Build official and configured external player news/lineup evidence."""
    try:
        catalog = PlayerEvidenceStore(data_dir()).build()
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))


@app.command()
def hermes_run(
    objective_mode: ObjectiveMode | None = typer.Option(
        None,
        help="Explicitly select POINTS_MODE or RANK_MODE for this planning cycle.",
    ),
) -> None:
    """Let Hermes set strategy and commit one autonomous backend-validated decision."""
    try:
        result = HermesManager(data_dir()).run_current(objective_mode=objective_mode)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(result))


@app.command()
def hermes_reinitialize_opening_squad(
    force: bool = typer.Option(False, "--force",
                               help="Rebuild the opening squad even when it already carries a committed plan"),
) -> None:
    """Replace a legacy pre-season opening state with the horizon-derived squad."""
    try:
        result = HermesManager(data_dir()).reinitialize_current(force=force)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    if result is None:
        typer.echo(json_dumps({"status": "skipped"}))
        raise typer.Exit(code=2)
    typer.echo(json_dumps(result))


@app.command()
def hermes_state() -> None:
    """Show Hermes' latest autonomous strategy and squad state."""
    try:
        state = HermesManager(data_dir()).latest_state()
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(state))


@app.command()
def hermes_decision() -> None:
    """Show Hermes' latest audited decision."""
    try:
        decision = HermesManager(data_dir()).latest_decision()
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(decision))


@app.command()
def hermes_migrate_state(
    purchase_prices_file: Path = typer.Argument(..., exists=True, readable=True),
    gameweek: int = typer.Option(..., min=1, max=38), season_id: str = typer.Option(...),
) -> None:
    """Explicitly migrate a legacy Hermes state with exact purchase prices."""
    try:
        prices = {int(key): int(value) for key, value in json.loads(purchase_prices_file.read_text(encoding="utf-8")).items()}
        result = HermesManager(data_dir()).migrate_legacy_state(prices, gameweek, season_id)
    except (OSError, RuntimeError, ValueError, FileNotFoundError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(result))


@app.command()
def hermes_replan_current(
    reason: str = typer.Option(..., help="Why the current decision must be replaced."),
    active_chip: str | None = typer.Option(
        None, help="Optional confirmed chip: wildcard | free_hit | bench_boost | triple_captain",
    ),
    active_chip_set: int | None = typer.Option(
        None, min=1, max=2, help="Chip set for the optional active chip.",
    ),
) -> None:
    """Safely supersede an unexecuted decision for the current gameweek."""
    try:
        result = HermesManager(data_dir()).replan_current(
            reason, active_chip=active_chip, active_chip_set=active_chip_set,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(result))


@app.command()
def player_evidence(limit: int = typer.Option(100, min=1, max=5000)) -> None:
    """List the latest normalized player evidence records."""
    try:
        rows = PlayerEvidenceStore(data_dir()).latest()
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(rows[:limit]))


@app.command()
def tavily_news_preview() -> None:
    """Show the latest Tavily player-news assessments without changing decisions."""
    try:
        payload = TavilyNewsStore(data_dir()).latest_payload()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(payload))


@app.command()
def chip_advice() -> None:
    """Show the latest read-only chip recommendations."""
    try:
        advice = ChipAdviceStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(advice))


@app.command()
def chip_state(
    season_id: str = typer.Option(..., help="Season ID, e.g. 2026-27"),
    chip: str = typer.Option(..., help="wildcard | free_hit | bench_boost | triple_captain"),
    chip_set: int = typer.Option(1, min=1, max=2, help="1 = GW1-19 set, 2 = GW20-38 set"),
    gameweek: int = typer.Option(..., min=1, max=38),
    mark_used: bool = typer.Option(False, "--mark-used", help="Record that this chip slot was played"),
) -> None:
    """Show the chip ledger, or record a played chip slot."""
    store = ChipStateStore(data_dir())
    if mark_used:
        state = store.mark_used(season_id, chip, chip_set, gameweek)
        typer.echo(json_dumps(state))
        return
    typer.echo(json_dumps(store.latest(season_id)))


@app.command()
def fetch_event_markets() -> None:
    """Fetch configured EPL team-total and player-prop markets for matched fixtures."""
    try:
        schedule = DeadlineScheduler(data_dir()).status()
        consensus = FixtureOddsConsensusStore(data_dir()).latest()
        event_ids = [row.odds_event_id for row in consensus if row.gameweek == schedule.event]
        if not event_ids:
            event_ids = [row.odds_event_id for row in consensus]
        catalog = EventMarketStore(data_dir()).fetch(event_ids)
    except (FileNotFoundError, OddsSourceError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))


@app.command()
def build_market_signals() -> None:
    """Build strict clean-sheet and complete player-prop probability signals."""
    try:
        catalog = MarketSignalStore(data_dir()).build()
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(catalog))


@app.command()
def check_source_health() -> None:
    """Validate source schemas and persist a freshness report."""
    try:
        report = SourceHealthChecker(data_dir()).run()
    except ValueError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(report))


@app.command()
def latest_source_health() -> None:
    """Show the latest persisted source health report."""
    try:
        report = SourceHealthChecker(data_dir()).latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(report))


@app.command()
def team_aliases() -> None:
    """Validate and show the effective fixture-to-odds team aliases."""
    try:
        aliases, path = load_team_aliases(data_dir())
    except ValueError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps({"aliases": aliases, "configuration_path": str(path) if path else None}))


@app.command()
def refresh_current_data(
    start_gameweek: int = typer.Option(..., min=1, max=38),
    end_gameweek: int = typer.Option(..., min=1, max=38),
    budget: int = typer.Option(1000, min=0),
) -> None:
    """Run and audit the complete current-data-to-recommendation cycle."""
    try:
        result = CurrentDataRefreshJob(data_dir()).run(start_gameweek, end_gameweek, budget)
    except RefreshJobError as exc:
        typer.echo(json_dumps(exc.result))
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(result))


@app.command()
def latest_refresh_job() -> None:
    """Show the latest audited current-data refresh job."""
    try:
        result = CurrentDataRefreshJob(data_dir()).latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(result))


@app.command()
def scheduler_status() -> None:
    """Show the next FPL deadline and scheduled refresh time."""
    try:
        status = DeadlineScheduler(data_dir()).status()
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(status))


@app.command()
def run_scheduler_tick(force: bool = typer.Option(False, help="Run before the configured refresh time")) -> None:
    """Run one duplicate-safe deadline scheduler tick."""
    try:
        result = DeadlineScheduler(data_dir()).tick(force=force)
    except SchedulerTickError as exc:
        typer.echo(json_dumps(exc.result))
        raise typer.Exit(code=1) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(result))


@app.command()
def run_deadline_scheduler() -> None:
    """Run the deadline scheduler continuously using the configured poll interval."""
    DeadlineScheduler(data_dir()).run_forever()


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
    """Show the most recently saved bootstrap-static snapshot."""
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
    data_cutoff: str = typer.Option(..., help="Timezone-aware ISO-8601 maximum fixture kickoff time"),
) -> None:
    """Evaluate a leakage-safe player rolling-average points baseline."""
    try:
        selected_import = import_id or HistoricalSeasonImporter(data_dir()).latest_import_id(season)
        cutoff = datetime.fromisoformat(data_cutoff.replace("Z", "+00:00"))
        summary = BaselineBacktester(data_dir()).run(season, selected_import, start_gameweek, end_gameweek, cutoff, window)
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
def optimize_current_squad(
    budget: int = typer.Option(1000, min=0),
    projection_source: ProjectionSource = typer.Option(ProjectionSource.CURRENT),
    catalog_id: str | None = typer.Option(None, help="Exact fixture/odds projection JSONL filename"),
    differential_appetite: float = typer.Option(0.0, min=0, max=1,
                                                 help="Prefer low-owned players when projections are near-tied (0..1)"),
    objective_mode: ObjectiveMode = typer.Option("POINTS_MODE"),
) -> None:
    """Choose the exact highest-projected legal squad from all current FPL players."""
    try:
        game_state, templates = _rank_context(objective_mode)
        squad = optimize_squad(
            load_projection_candidates(data_dir(), projection_source, catalog_id), budget,
            differential_appetite=differential_appetite,
            objective_mode=objective_mode, game_state=game_state, template_states=templates,
        )
    except (FileNotFoundError, SquadOptimizationError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(squad))


@app.command()
def plan_transfers(
    squad_file: Path = typer.Argument(..., exists=True, readable=True),
    projection_source: ProjectionSource = typer.Option(ProjectionSource.CURRENT),
    catalog_id: str | None = typer.Option(None, help="Exact fixture/odds projection JSONL filename"),
    objective_mode: ObjectiveMode = typer.Option("POINTS_MODE"),
) -> None:
    """Compare hold and legal transfer plans for a current squad JSON state."""
    try:
        state = CurrentSquadState.model_validate(json.loads(squad_file.read_text(encoding="utf-8")))
        game_state, templates = _rank_context(objective_mode)
        plan = build_transfer_plan(
            load_projection_candidates(data_dir(), projection_source, catalog_id), state,
            objective_mode=objective_mode, game_state=game_state, template_states=templates,
        )
    except (OSError, ValueError, FileNotFoundError, SquadOptimizationError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(plan))


@app.command()
def plan_horizon(
    squad_file: Path = typer.Argument(..., exists=True, readable=True),
    catalog_id: str | None = typer.Option(None, help="Exact 1-6 GW odds projection JSONL filename"),
    pre_season: bool = typer.Option(False, help="Unlimited transfers for the opening gameweek only"),
    decision_hit_penalty: float = typer.Option(4.0, min=0,
                                                help="Projected-point penalty per transfer over the free allowance"),
    churn_penalty: float | None = typer.Option(None, min=0,
                                                help="Override the planned-transfer penalty (defaults to AIFPL_TRANSFER_PENALTY)"),
    objective_mode: ObjectiveMode = typer.Option("POINTS_MODE"),
    active_chip: str | None = typer.Option(
        None, help="Optional first-week chip: wildcard | free_hit | bench_boost | triple_captain",
    ),
) -> None:
    """Optimize transfers, hits, free-transfer rollover, and bank across 1-6 gameweeks."""
    try:
        state = HorizonSquadState.model_validate(json.loads(squad_file.read_text(encoding="utf-8")))
        game_state, templates = _rank_context(objective_mode)
        plan = plan_horizon_transfers(
            calibrated_odds_rows(data_dir(), catalog_id)[0], state,
            decision_hit_penalty=decision_hit_penalty, pre_season=pre_season,
            churn_penalty=churn_penalty, objective_mode=objective_mode,
            game_state=game_state, template_states=templates,
            active_chip=active_chip,
        )
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


@app.command()
def fixture_projections(limit: int = typer.Option(20, min=1, max=1000)) -> None:
    """List rows from the latest persisted fixture projection catalog."""
    try:
        rows = FixtureProjectionStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(rows[:limit]))


@app.command()
def odds_projections(limit: int = typer.Option(20, min=1, max=1000)) -> None:
    """List rows from the latest persisted odds projection catalog."""
    try:
        rows = OddsProjectionStore(data_dir()).latest()
    except FileNotFoundError as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(rows[:limit]))


@app.command()
def notify_telegram() -> None:
    """Send the next-deadline recommendation digest to the configured Telegram chat."""
    from aifpl.notifier import TelegramNotifier, TelegramNotifierError, build_recommendation_message

    try:
        schedule = DeadlineScheduler(data_dir()).status()
        message = build_recommendation_message(data_dir(), schedule.event, schedule.season_id, schedule.deadline)
        TelegramNotifier.from_environment().send_message(message)
    except (ValueError, FileNotFoundError, TelegramNotifierError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps({"event": schedule.event, "sent": True, "message": message}))


@app.command()
def score_decisions(event: int | None = typer.Option(None, min=1, max=38)) -> None:
    """Score the latest Hermes decision against the completed gameweek's actuals."""
    from aifpl.hermes import HermesManager
    from aifpl.scoring import DecisionScorer

    try:
        decision = HermesManager(data_dir()).latest_decision()
        scorer = DecisionScorer(data_dir())
        target_gameweek = event or decision.gameweek
        scorer.require_final_gameweek(target_gameweek)
        record = scorer.score(decision.decision_path, event=target_gameweek)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps(record))


@app.command()
def send_scorecard() -> None:
    """Send the latest scored decision to the configured Telegram chat."""
    from aifpl.notifier import TelegramNotifier, TelegramNotifierError, build_scorecard_message

    try:
        message = build_scorecard_message(data_dir())
        TelegramNotifier.from_environment().send_message(message)
    except (ValueError, FileNotFoundError, TelegramNotifierError) as exc:
        raise typer.Exit(str(exc)) from exc
    typer.echo(json_dumps({"sent": True, "message": message}))
