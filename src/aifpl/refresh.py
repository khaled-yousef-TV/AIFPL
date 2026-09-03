from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock

from aifpl.artifacts import json_bytes, write_immutable
from aifpl.chips import ChipAdviceStore, ChipAdvisor, ChipIntelFetcher, ChipStateStore
from aifpl.current import CurrentPlayerCatalogStore
from aifpl.current_projections import CurrentProjectionStore
from aifpl.config import minimum_odds_fixture_coverage, partial_odds_fixture_coverage
from aifpl.fixture_projections import FixtureProjectionStore
from aifpl.fixtures import CurrentFixtureCatalogStore
from aifpl.fpl import FplClient
from aifpl.game_state import GameState, GameStateStore
from aifpl.health import SourceHealthChecker
from aifpl.live_calibration import current_season_id
from aifpl.odds import OddsSnapshotStore, TheOddsApiClient
from aifpl.odds_matching import FixtureOddsConsensusStore
from aifpl.odds_projections import OddsProjectionStore
from aifpl.player_evidence import PlayerEvidenceStore
from aifpl.market_odds import EventMarketStore
from aifpl.market_signals import MarketSignalStore
from aifpl.optimizer import optimize_squad
from aifpl.projection_catalogs import ProjectionSource, load_projection_candidates
from aifpl.snapshots import SnapshotStore
from aifpl.security import redact_secrets
from aifpl.tavily_news import TavilyNewsCatalog, TavilyNewsStore
from aifpl.template import PlayerTemplateState, TemplateCatalogStore
from aifpl.transfers import CurrentSquadState, plan_transfers
from aifpl.xg_projections import XgXaProjectionStore


class RefreshJobResult(BaseModel):
    status: Literal["succeeded", "failed"]
    started_at: datetime
    completed_at: datetime
    start_gameweek: int
    end_gameweek: int
    budget: int
    completed_steps: list[str]
    artifacts: dict[str, str]
    health_status: str | None = None
    recommendation: dict[str, object] | None = None
    odds_coverage: float | None = None
    odds_coverage_status: Literal["full", "partial"] | None = None
    error: str | None = None
    output_path: str


class RefreshJobError(RuntimeError):
    def __init__(self, result: RefreshJobResult) -> None:
        super().__init__(result.error or "Refresh job failed")
        self.result = result


class CurrentDataRefreshJob:
    def __init__(self, root: Path, fpl_client: FplClient | None = None, odds_client: TheOddsApiClient | None = None) -> None:
        self.root = root
        self.fpl_client = fpl_client or FplClient()
        self.odds_client = odds_client

    def run(self, start_gameweek: int, end_gameweek: int, budget: int = 1000) -> RefreshJobResult:
        if start_gameweek < 1 or end_gameweek > 38 or end_gameweek < start_gameweek:
            raise ValueError("gameweek range must be within 1..38 and start must not exceed end")
        if budget < 0:
            raise ValueError("budget must not be negative")
        started_at = datetime.now(timezone.utc)
        run_id = started_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = self.root / "jobs" / "refresh" / f"{run_id}.json"
        steps: list[str] = []
        artifacts: dict[str, str] = {}
        health_status: str | None = None
        lock_descriptor: int | None = None
        try:
            lock_path = self.root / "jobs" / "refresh" / "current.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                flock(lock_descriptor, LOCK_EX | LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another current-data refresh job is already running") from exc
            snapshots = SnapshotStore(self.root)
            bootstrap_payload = asyncio.run(self.fpl_client.fetch_bootstrap())
            bootstrap_path, bootstrap_summary = snapshots.save_bootstrap(bootstrap_payload)
            artifacts["bootstrap_snapshot"] = str(bootstrap_path)
            steps.append("fetch_bootstrap")

            fixture_path, _ = snapshots.save_fixtures(asyncio.run(self.fpl_client.fetch_fixtures()))
            artifacts["fixture_snapshot"] = str(fixture_path)
            steps.append("fetch_fixtures")

            if bootstrap_summary.current_event is not None:
                event_path, _ = snapshots.save_event_live(
                    bootstrap_summary.current_event,
                    asyncio.run(self.fpl_client.fetch_event_live(bootstrap_summary.current_event)),
                )
                artifacts["event_snapshot"] = str(event_path)
                steps.append("fetch_event_live")

            odds_client = self.odds_client or TheOddsApiClient.from_environment()
            odds_payload, odds_headers = odds_client.fetch_epl_h2h()
            odds = OddsSnapshotStore(self.root).save_epl_h2h(odds_payload, odds_headers)
            artifacts["odds_snapshot"] = odds.raw_path
            artifacts["normalized_odds"] = odds.normalized_path
            steps.append("fetch_odds")

            players = CurrentPlayerCatalogStore(self.root).normalize(bootstrap_path)
            fixtures = CurrentFixtureCatalogStore(self.root).normalize(fixture_path)
            artifacts["player_catalog"] = players.output_path
            artifacts["fixture_catalog"] = fixtures.output_path
            steps.append("normalize_catalogs")
            player_catalog_path = Path(players.output_path)
            fixture_catalog_path = Path(fixtures.output_path)
            current_players = CurrentPlayerCatalogStore(self.root).load(player_catalog_path)
            hermes_state = _current_hermes_state(self.root, current_players)
            owned_news = _research_owned_players(
                self.root, current_players, hermes_state, start_gameweek,
            )
            evidence_sources = {}
            if owned_news is not None and owned_news.output_path is not None:
                artifacts["tavily_owned_news"] = owned_news.output_path
                evidence_sources["tavily_owned_news"] = Path(owned_news.output_path)
                steps.append("research_owned_player_news")
            evidence = PlayerEvidenceStore(self.root).build(
                player_catalog_path,
                additional_records=owned_news.evidence_records if owned_news is not None else None,
                additional_sources=evidence_sources,
            )
            artifacts["player_evidence"] = evidence.output_path
            steps.append("build_player_evidence")

            current = CurrentProjectionStore(self.root).build(player_catalog_path)
            xg = XgXaProjectionStore(self.root).build(player_catalog_path)
            fixture_projection = FixtureProjectionStore(self.root).build(
                start_gameweek, end_gameweek, player_catalog_path, fixture_catalog_path,
            )
            artifacts["current_projections"] = current.output_path
            artifacts["xg_xa_projections"] = xg.output_path
            artifacts["fixture_projections"] = fixture_projection.output_path
            steps.append("build_base_projections")

            consensus_store = FixtureOddsConsensusStore(self.root)
            consensus = consensus_store.build_latest(
                player_catalog_path, fixture_catalog_path, Path(odds.normalized_path),
            )
            consensus_rows = consensus_store.load(Path(consensus.output_path))
            signal_path: Path | None = None
            if os.environ.get("AIFPL_FETCH_EVENT_MARKETS", "false").lower() in ("1", "true", "yes"):
                event_ids = [row.odds_event_id for row in consensus_rows if row.gameweek == start_gameweek]
                if not event_ids:
                    event_ids = [row.odds_event_id for row in consensus_rows]
                if event_ids:
                    market_catalog = EventMarketStore(self.root).fetch(event_ids, odds_client)
                    artifacts["event_markets"] = market_catalog.output_path
                    signals = MarketSignalStore(self.root).build(
                        player_catalog_path, Path(consensus.output_path), Path(market_catalog.output_path),
                    )
                    signal_path = Path(signals.output_path)
                    artifacts["market_signals"] = signals.output_path
                    steps.append("build_market_signals")
            odds_projection = OddsProjectionStore(self.root).build(
                start_gameweek, end_gameweek, player_catalog_path, fixture_catalog_path, Path(consensus.output_path),
                Path(evidence.output_path),
                signal_path,
            )
            candidate_news = _research_transfer_candidates(
                self.root,
                current_players,
                hermes_state,
                owned_news,
                Path(odds_projection.output_path).name,
                start_gameweek,
            )
            if candidate_news is not None and candidate_news.output_path is not None:
                artifacts["tavily_candidate_news"] = candidate_news.output_path
                steps.append("research_transfer_candidate_news")
                all_records = (owned_news.evidence_records if owned_news is not None else []) + candidate_news.evidence_records
                all_sources = dict(evidence_sources)
                all_sources["tavily_candidate_news"] = Path(candidate_news.output_path)
                evidence = PlayerEvidenceStore(self.root).build(
                    player_catalog_path,
                    additional_records=all_records,
                    additional_sources=all_sources,
                )
                artifacts["player_evidence"] = evidence.output_path
                odds_projection = OddsProjectionStore(self.root).build(
                    start_gameweek, end_gameweek, player_catalog_path, fixture_catalog_path, Path(consensus.output_path),
                    Path(evidence.output_path),
                    signal_path,
                )
            artifacts["fixture_odds_consensus"] = consensus.output_path
            artifacts["odds_projections"] = odds_projection.output_path
            steps.append("build_odds_projections")

            fixture_rows = CurrentFixtureCatalogStore(self.root).load(fixture_catalog_path)
            relevant_ids = {
                fixture.id for fixture in fixture_rows
                if not fixture.finished and fixture.gameweek is not None
                and fixture.gameweek == start_gameweek
            }
            matched_ids = {row.fixture_id for row in consensus_rows}
            coverage = len(relevant_ids & matched_ids) / len(relevant_ids) if relevant_ids else 0.0
            if not relevant_ids or coverage < minimum_odds_fixture_coverage():
                raise ValueError(
                    f"Odds fixture coverage {coverage:.1%} is below the hard floor "
                    f"{minimum_odds_fixture_coverage():.1%}"
                )
            coverage_status: Literal["full", "partial"] = (
                "full" if coverage >= partial_odds_fixture_coverage() else "partial"
            )
            if coverage_status == "partial":
                steps.append(f"validate_odds_coverage:partial:{coverage:.1%}")
            else:
                steps.append("validate_odds_coverage")

            health = SourceHealthChecker(self.root).run()
            health_status = health.overall_status
            artifacts["source_health"] = health.output_path
            steps.append("check_source_health")
            if health.overall_status != "healthy":
                raise ValueError("Refusing recommendation because refreshed source health is degraded")

            objective_mode, rank_state, template_states = _rank_inputs(self.root, hermes_state)
            recommendation = optimize_squad(
                load_projection_candidates(
                    self.root, ProjectionSource.ODDS, Path(odds_projection.output_path).name,
                ), budget, objective_mode=objective_mode, game_state=rank_state,
                template_states=template_states,
            )
            steps.append("optimize_squad")
            chip_advice_path = _build_chip_advice(
                self.root, current_players, hermes_state, recommendation,
                Path(odds_projection.output_path).name, start_gameweek,
            )
            if chip_advice_path is not None:
                artifacts["chip_advice"] = str(chip_advice_path)
                steps.append("build_chip_advice")
            result = RefreshJobResult(
                status="succeeded", started_at=started_at, completed_at=datetime.now(timezone.utc),
                start_gameweek=start_gameweek, end_gameweek=end_gameweek, budget=budget,
                completed_steps=steps, artifacts=artifacts, health_status=health_status,
                recommendation=asdict(recommendation), output_path=str(output_path),
                odds_coverage=round(coverage, 4), odds_coverage_status=coverage_status,
            )
        except Exception as exc:
            result = RefreshJobResult(
                status="failed", started_at=started_at, completed_at=datetime.now(timezone.utc),
                start_gameweek=start_gameweek, end_gameweek=end_gameweek, budget=budget,
                completed_steps=steps, artifacts=artifacts,
                health_status=health_status,
                error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                output_path=str(output_path),
            )
            write_immutable(output_path, json_bytes(result.model_dump(mode="json"), pretty=True))
            raise RefreshJobError(result) from exc
        finally:
            if lock_descriptor is not None:
                flock(lock_descriptor, LOCK_UN)
                os.close(lock_descriptor)
        write_immutable(output_path, json_bytes(result.model_dump(mode="json"), pretty=True))
        return result

    def latest(self) -> RefreshJobResult:
        directory = self.root / "jobs" / "refresh"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No current-data refresh job has run yet")
        return RefreshJobResult.model_validate_json(files[-1].read_text(encoding="utf-8"))


def _current_hermes_state(root: Path, players: list) -> "object | None":
    """Return the current-season Hermes squad state if it is fully valid."""
    from aifpl.hermes import HermesManager

    try:
        state = HermesManager(root).latest_state(optional=True)
    except (FileNotFoundError, ValueError):
        return None
    if state is None or not state.squad.player_ids:
        return None
    if state.season_id and state.season_id != current_season_id(root):
        return None
    squad_ids = set(state.squad.player_ids)
    if len(squad_ids) != 15:
        return None
    catalog_ids = {player.id for player in players}
    if not squad_ids <= catalog_ids:
        return None
    return state


def _research_owned_players(
    root: Path,
    players: list,
    state: "object | None",
    start_gameweek: int,
) -> TavilyNewsCatalog | None:
    if state is None:
        return None
    season_id = state.season_id or current_season_id(root) or ""
    return TavilyNewsStore(root).research(
        players,
        state.squad.player_ids,
        start_gameweek,
        season_id,
        query_kind="owned",
    )


def _research_transfer_candidates(
    root: Path,
    players: list,
    state: "object | None",
    owned_news: TavilyNewsCatalog | None,
    catalog_id: str,
    start_gameweek: int,
) -> TavilyNewsCatalog | None:
    if state is None or owned_news is None or not owned_news.actionable_player_ids:
        return None
    candidates = load_projection_candidates(root, ProjectionSource.ODDS, catalog_id)
    squad_state = CurrentSquadState(
        player_ids=list(state.squad.player_ids),
        bank=state.squad.bank,
        free_transfers=state.squad.free_transfers,
        max_transfers=2,
    )
    try:
        objective_mode, rank_state, template_states = _rank_inputs(root, state)
        transfer_plan = plan_transfers(
            candidates, squad_state, objective_mode=objective_mode,
            game_state=rank_state, template_states=template_states,
        )
    except Exception:
        return None
    incoming_ids = [player.player_id for player in transfer_plan.incoming]
    if not incoming_ids:
        return None
    season_id = state.season_id or current_season_id(root) or ""
    return TavilyNewsStore(root).research(
        players,
        incoming_ids,
        start_gameweek,
        season_id,
        query_kind="candidate",
    )


def _build_chip_advice(
    root: Path,
    players: list,
    state: "object | None",
    best_squad: object,
    catalog_id: str,
    start_gameweek: int,
) -> Path | None:
    from aifpl.chips import ChipAdviceStore, ChipAdvisor, ChipIntelFetcher, ChipStateStore
    from aifpl.config import chip_settings
    from aifpl.fixtures import CurrentFixtureCatalogStore
    from aifpl.odds_projections import OddsProjectionStore

    if state is None or not hasattr(best_squad, "players"):
        return None
    season_id = state.season_id or current_season_id(root) or ""
    if not season_id:
        return None
    settings = chip_settings()
    fixtures = CurrentFixtureCatalogStore(root).latest()
    projections = OddsProjectionStore(root).latest(catalog_id)
    if settings.intel_enabled:
        try:
            intel = ChipIntelFetcher(root).fetch(start_gameweek)
        except Exception:
            try:
                intel = ChipIntelFetcher(root).latest()
            except FileNotFoundError:
                from aifpl.chips import ChipIntel

                intel = ChipIntel(fetched_at=datetime.now(timezone.utc), stale=True)
    else:
        try:
            intel = ChipIntelFetcher(root).latest()
        except FileNotFoundError:
            from aifpl.chips import ChipIntel

            intel = ChipIntel(fetched_at=datetime.now(timezone.utc), stale=True)
    state_store = ChipStateStore(root)
    chip_state = state_store.latest(season_id)
    advice = ChipAdvisor(settings).evaluate(
        season_id,
        start_gameweek,
        chip_state,
        fixtures,
        projections,
        list(state.squad.player_ids),
        list(state.starting_xi_ids),
        [player.player_id for player in best_squad.players],
        intel,
        game_state=getattr(state, "game_state", None),
        template_states=_latest_template_states(
            root,
            getattr(getattr(state, "game_state", None), "season_id", None),
            getattr(getattr(state, "game_state", None), "gameweek", None),
        ),
    )
    saved = ChipAdviceStore(root).save(advice)
    return Path(saved.output_path)


def _latest_template_states(
    root: Path, season_id: str | None = None, gameweek: int | None = None,
) -> dict[int, PlayerTemplateState]:
    try:
        return {
            row.player_id: row
            for row in TemplateCatalogStore(root).latest(
                season_id=season_id, gameweek=gameweek,
            ).players
        }
    except FileNotFoundError:
        if gameweek is not None:
            try:
                return {
                    row.player_id: row
                    for row in TemplateCatalogStore(root).latest(season_id=season_id).players
                }
            except FileNotFoundError:
                pass
        return {}


def _rank_inputs(
    root: Path, hermes_state: object | None,
) -> tuple[str, GameState | None, dict[int, PlayerTemplateState]]:
    strategy = getattr(hermes_state, "strategy", None)
    objective_mode = getattr(strategy, "objective_mode", None)
    season_id = getattr(hermes_state, "season_id", None) or current_season_id(root)
    if objective_mode is None:
        try:
            account_state = GameStateStore(root).latest(season_id=season_id)
        except FileNotFoundError:
            account_state = None
        if account_state is not None and account_state.rank_data_available:
            objective_mode = "RANK_MODE"
        else:
            objective_mode = "POINTS_MODE"
    if objective_mode == "POINTS_MODE":
        return objective_mode, None, {}
    game_state = getattr(hermes_state, "game_state", None)
    if game_state is None:
        try:
            game_state = GameStateStore(root).latest(season_id=season_id)
        except FileNotFoundError:
            return "POINTS_MODE", None, {}
    if not game_state.rank_data_available:
        return "POINTS_MODE", None, {}
    game_state = game_state.model_copy(update={"objective_mode": "RANK_MODE"})
    return objective_mode, game_state, _latest_template_states(
        root, game_state.season_id, game_state.gameweek,
    )
