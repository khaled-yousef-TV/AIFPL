from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.fixture_projections import DIFFICULTY_MULTIPLIERS
from aifpl.fixtures import CurrentFixture, CurrentFixtureCatalogStore
from aifpl.odds_matching import FixtureOddsConsensus, FixtureOddsConsensusStore
from aifpl.xg_projections import elapsed_gameweeks, xg_xa_blend
from aifpl.player_evidence import PlayerEvidenceStore, late_return_adjustments, predicted_start_probabilities
from aifpl.market_signals import MarketSignalStore, PlayerPropSignal, TeamCleanSheetSignal
from aifpl.transfer_awareness import TransferProfile, TransferAwarenessStore
from aifpl.ownership import configured_effective_ownership
from aifpl.artifacts import complete_artifact_paths, jsonl_bytes, verify_artifact, verify_lineage, write_immutable, write_manifest
from aifpl.model_identity import model_identity
from aifpl.template import PlayerTemplateState


ODDS_PROJECTION_METHOD = "fpl_xg_xa_blend_v2.fixture_difficulty_v1.match_odds_v1.availability_evidence_v1.market_signals_v1"
ODDS_WIN_WEIGHT = 0.4


@dataclass(frozen=True)
class OddsAdjustedGameweekProjection:
    player_id: int
    player_name: str
    position: str
    club: str
    cost: int
    gameweek: int
    fixture_count: int
    odds_backed_fixture_count: int
    projected_points: float
    methodology: str = ODDS_PROJECTION_METHOD
    clean_sheet_probability: float | None = None
    assist_probability: float | None = None
    selected_by_percent: float = 0.0
    expected_minutes: float | None = None
    start_probability: float | None = None
    availability_multiplier: float | None = None
    effective_ownership_pct: float | None = None
    expected_captaincy: float | None = None
    template_score: float | None = None
    template_status: str | None = None


@dataclass(frozen=True)
class OddsProjectionCatalog:
    start_gameweek: int
    end_gameweek: int
    records: int
    output_path: str
    created_at: datetime
    source_player_catalog: str | None = None
    source_fixture_catalog: str | None = None
    source_consensus_catalog: str | None = None
    source_evidence_catalog: str | None = None
    methodology: str = ODDS_PROJECTION_METHOD
    manifest_path: str | None = None
    odds_coverage_by_gameweek: dict[int, float] | None = None
    odds_coverage_status: str | None = None


def build_odds_adjusted_projections(
    players: list[CurrentPlayer], fixtures: list[CurrentFixture], consensus: list[FixtureOddsConsensus], start_gameweek: int, end_gameweek: int,
    gameweeks_elapsed: int | None = None,
    start_probability_by_player: dict[tuple[int, int | None], float] | None = None,
    clean_sheet_signals: list[TeamCleanSheetSignal] | None = None,
    player_prop_signals: list[PlayerPropSignal] | None = None,
    player_prop_weight: float = 0.0,
    transfer_profiles: dict[int, TransferProfile] | None = None,
    late_return_evidence: dict[tuple[int, int], tuple[float, float]] | None = None,
    template_states: Mapping[int, PlayerTemplateState] | None = None,
) -> list[OddsAdjustedGameweekProjection]:
    if start_gameweek < 1 or end_gameweek < start_gameweek:
        raise ValueError("gameweek range is invalid")
    configured_eo = configured_effective_ownership()
    consensus_by_fixture = {row.fixture_id: row for row in consensus}
    gameweeks_elapsed = gameweeks_elapsed or max(1, max((player.starts for player in players), default=0))
    fixtures_by_team_gameweek: dict[tuple[int, int], list[CurrentFixture]] = {}
    clean_by_fixture_team = {(row.fixture_id, row.team_name): row.probability for row in clean_sheet_signals or []}
    assist_by_fixture_player = {(row.fixture_id, row.player_id): row.probability for row in player_prop_signals or [] if row.event_type == "assist"}
    for fixture in fixtures:
        if fixture.finished or fixture.gameweek is None or not start_gameweek <= fixture.gameweek <= end_gameweek:
            continue
        fixtures_by_team_gameweek.setdefault((fixture.home_team_id, fixture.gameweek), []).append(fixture)
        fixtures_by_team_gameweek.setdefault((fixture.away_team_id, fixture.gameweek), []).append(fixture)
    projections: list[OddsAdjustedGameweekProjection] = []
    for player in players:
        for gameweek in range(start_gameweek, end_gameweek + 1):
            player_fixtures = fixtures_by_team_gameweek.get((player.club_id, gameweek), [])
            total = 0.0
            odds_backed = 0
            clean_probabilities: list[float] = []
            assist_probabilities: list[float] = []
            expected_minutes_total = 0.0
            start_probability: float | None = None
            availability_multiplier: float | None = None
            for fixture in player_fixtures:
                late = (late_return_evidence or {}).get((player.id, gameweek))
                start_override = None
                if late is not None:
                    start_override = late[0]
                elif gameweek == start_gameweek:
                    start_override = (start_probability_by_player or {}).get(
                        (player.id, fixture.id), (start_probability_by_player or {}).get((player.id, None))
                    )
                blend = xg_xa_blend(
                    player, gameweeks_elapsed,
                    apply_next_round_availability=gameweek == start_gameweek,
                    start_probability_override=start_override,
                    transfer_profile=(transfer_profiles or {}).get(player.id),
                    minutes_multiplier_override=late[1] if late is not None else None,
                )
                base = blend.projected_points
                participation = blend.expected_minutes / 90
                expected_minutes_total += blend.expected_minutes
                if start_probability is None:
                    opportunities = gameweeks_elapsed or max(1, player.starts)
                    start_probability = (
                        start_override if start_override is not None else min(1.0, player.starts / opportunities)
                    )
                    availability_multiplier = (
                        (player.chance_of_playing_next_round / 100)
                        if gameweek == start_gameweek and player.chance_of_playing_next_round is not None else 1.0
                    )
                difficulty = fixture.home_difficulty if player.club_id == fixture.home_team_id else fixture.away_difficulty
                multiplier = DIFFICULTY_MULTIPLIERS[difficulty]
                market = consensus_by_fixture.get(fixture.id)
                if market is not None:
                    win_probability = market.home_win_probability if player.club_id == fixture.home_team_id else market.away_win_probability
                    multiplier *= 1 + ODDS_WIN_WEIGHT * (win_probability - 0.5)
                    odds_backed += 1
                total += base * multiplier
                clean_probability = clean_by_fixture_team.get((fixture.id, player.club))
                if clean_probability is not None and player.position in ("GK", "DEF", "MID"):
                    total += 0.4 * clean_probability * participation * (4 if player.position in ("GK", "DEF") else 1)
                    clean_probabilities.append(clean_probability)
                assist_probability = assist_by_fixture_player.get((fixture.id, player.id))
                if assist_probability is not None:
                    total += player_prop_weight * assist_probability * participation * 3
                    assist_probabilities.append(assist_probability)
            template_eo = _template_value(template_states, player.id, "effective_ownership")
            projections.append(OddsAdjustedGameweekProjection(
                player_id=player.id, player_name=player.name, position=player.position, club=player.club, cost=player.cost,
                gameweek=gameweek, fixture_count=len(player_fixtures), odds_backed_fixture_count=odds_backed,
                projected_points=round(total, 4),
                clean_sheet_probability=round(sum(clean_probabilities) / len(clean_probabilities), 4) if clean_probabilities else None,
                assist_probability=round(sum(assist_probabilities) / len(assist_probabilities), 4) if assist_probabilities else None,
                selected_by_percent=player.selected_by_percent,
                expected_minutes=round(expected_minutes_total, 4) if player_fixtures else None,
                start_probability=round(start_probability, 4) if start_probability is not None else None,
                availability_multiplier=round(availability_multiplier, 4) if availability_multiplier is not None else None,
                 effective_ownership_pct=template_eo if template_eo is not None else configured_eo.get(player.id),
                expected_captaincy=_template_value(template_states, player.id, "expected_captaincy"),
                template_score=_template_value(template_states, player.id, "template_score"),
                template_status=_template_status(template_states, player.id),
            ))
    return projections


class OddsProjectionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build(
        self, start_gameweek: int, end_gameweek: int,
        player_catalog_path: Path | None = None, fixture_catalog_path: Path | None = None,
        consensus_catalog_path: Path | None = None,
        evidence_catalog_path: Path | None = None,
        market_signal_path: Path | None = None,
    ) -> OddsProjectionCatalog:
        player_store = CurrentPlayerCatalogStore(self.root)
        fixture_store = CurrentFixtureCatalogStore(self.root)
        player_path = player_catalog_path or player_store.latest_path()
        fixture_path = fixture_catalog_path or fixture_store.latest_path()
        players = player_store.load(player_path)
        fixtures = fixture_store.load(fixture_path)
        consensus_store = FixtureOddsConsensusStore(self.root)
        consensus_path = consensus_catalog_path or consensus_store.latest_path()
        consensus = consensus_store.load(consensus_path)
        verify_lineage(
            self.root, consensus_path,
            {"player_catalog": player_path, "fixture_catalog": fixture_path},
        )
        evidence_path: Path | None = evidence_catalog_path
        start_probabilities: dict[tuple[int, int | None], float] = {}
        late_return_evidence: dict[tuple[int, int], tuple[float, float]] = {}
        evidence_cutoff: datetime | None = None
        max_evidence_age: float | None = None
        evidence_store = PlayerEvidenceStore(self.root)
        if evidence_path is None:
            try:
                evidence_path = evidence_store.latest_path()
            except FileNotFoundError:
                pass
        if evidence_path is not None:
            evidence_cutoff = datetime.now(timezone.utc)
            season_id = _season_id(fixtures)
            max_age = float(__import__("os").environ.get("AIFPL_EVIDENCE_MAX_AGE_HOURS", "168"))
            if not __import__("math").isfinite(max_age) or max_age <= 0:
                raise ValueError("AIFPL_EVIDENCE_MAX_AGE_HOURS must be a positive finite value")
            max_evidence_age = max_age
            evidence_records = evidence_store.load(evidence_path, player_path)
            start_probabilities = predicted_start_probabilities(
                evidence_records, start_gameweek, season_id, evidence_cutoff, max_age,
            )
            late_return_evidence = late_return_adjustments(
                evidence_records, season_id, evidence_cutoff, max_age,
            )
        clean_signals: list[TeamCleanSheetSignal] = []
        prop_signals: list[PlayerPropSignal] = []
        signal_path = market_signal_path
        signal_store = MarketSignalStore(self.root)
        if signal_path is None:
            try:
                candidate_signal_path = signal_store.latest_path()
                clean_signals, prop_signals = signal_store.load(candidate_signal_path, player_path, consensus_path)
                signal_path = candidate_signal_path
            except (FileNotFoundError, ValueError):
                pass
        else:
            clean_signals, prop_signals = signal_store.load(signal_path, player_path, consensus_path)
        prop_weight = float(__import__("os").environ.get("AIFPL_PLAYER_PROP_WEIGHT", "0"))
        if not __import__("math").isfinite(prop_weight) or not 0 <= prop_weight <= 1:
            raise ValueError("AIFPL_PLAYER_PROP_WEIGHT must be a finite value within 0..1")
        transfer_profiles = TransferAwarenessStore(self.root).latest(players)
        season_id = _season_id(fixtures)
        projections = build_odds_adjusted_projections(
            players, fixtures, consensus, start_gameweek, end_gameweek,
            elapsed_gameweeks(self.root, player_path, players),
            start_probabilities,
            clean_signals, prop_signals, prop_weight,
            transfer_profiles,
            late_return_evidence,
            _latest_template_states(self.root, season_id, start_gameweek),
        )
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = self.root / "normalized" / "current" / "odds_projections" / f"gw{start_gameweek}-{end_gameweek}.{run_id}.{ODDS_PROJECTION_METHOD}.jsonl"
        write_immutable(output_path, jsonl_bytes(projections))
        new_signings = sum(
            1 for profile in transfer_profiles.values() if profile.is_new_signing
        )
        coverage_by_gameweek = {
            gameweek: round(
                sum(row.odds_backed_fixture_count for row in projections if row.gameweek == gameweek)
                / max(1, sum(row.fixture_count for row in projections if row.gameweek == gameweek)), 4,
            )
            for gameweek in range(start_gameweek, end_gameweek + 1)
        }
        from aifpl.config import partial_odds_fixture_coverage

        partial_threshold = partial_odds_fixture_coverage()
        coverage_status = (
            "full"
            if all(value >= partial_threshold for value in coverage_by_gameweek.values())
            else "partial"
        )
        sources = {"player_catalog": player_path, "fixture_catalog": fixture_path, "fixture_odds_consensus": consensus_path}
        if evidence_path is not None:
            sources["player_evidence"] = evidence_path
        if signal_path is not None:
            sources["market_signals"] = signal_path
        manifest_path = write_manifest(
            self.root, output_path, artifact_type="odds_projections", created_at=created_at.isoformat(),
            record_count=len(projections),
            sources=sources,
            methodology=ODDS_PROJECTION_METHOD,
            parameters={"start_gameweek": start_gameweek, "end_gameweek": end_gameweek,
                        "gameweeks_elapsed": elapsed_gameweeks(self.root, player_path, players),
                        "odds_win_weight": ODDS_WIN_WEIGHT, "player_prop_weight": prop_weight,
                        "new_signing_count": new_signings,
                        "model_identity": model_identity(),
                        "evidence_cutoff": evidence_cutoff.isoformat() if evidence_cutoff else None,
                        "max_evidence_age_hours": max_evidence_age,
                        "odds_coverage_by_gameweek": coverage_by_gameweek,
                        "odds_coverage_status": coverage_status},
        )
        return OddsProjectionCatalog(
            start_gameweek, end_gameweek, len(projections), str(output_path), created_at,
            str(player_path), str(fixture_path), str(consensus_path), str(evidence_path) if evidence_path else None,
            ODDS_PROJECTION_METHOD, str(manifest_path),
            coverage_by_gameweek, coverage_status,
        )

    def latest_path(self) -> Path:
        directory = self.root / "normalized" / "current" / "odds_projections"
        files = complete_artifact_paths(list(directory.glob("*.jsonl"))) if directory.exists() else []
        files = [path for path in files if self._is_raw_catalog(path)]
        if not files:
            raise FileNotFoundError("No odds projections exist; run build-odds-projections first")
        return max(files, key=lambda path: path.name.split(".")[1] if len(path.name.split(".")) >= 4 else path.name)

    def latest(self, catalog_id: str | None = None) -> list[OddsAdjustedGameweekProjection]:
        path = self._catalog_path(catalog_id) if catalog_id else self.latest_path()
        verify_artifact(self.root, path, require_manifest=catalog_id is not None)
        return [OddsAdjustedGameweekProjection(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]

    def _catalog_path(self, catalog_id: str) -> Path:
        if Path(catalog_id).name != catalog_id or not catalog_id.endswith(".jsonl"):
            raise ValueError("catalog_id must be a projection JSONL filename")
        path = self.root / "normalized" / "current" / "odds_projections" / catalog_id
        if not path.exists():
            raise FileNotFoundError(f"Odds projection catalog does not exist: {catalog_id}")
        return path

    @staticmethod
    def _is_raw_catalog(path: Path) -> bool:
        manifest_path = path.with_suffix(".manifest.json")
        if not manifest_path.exists():
            return True
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8")).get("artifact_type") != "calibrated_odds_projections"
        except (OSError, json.JSONDecodeError):
            return True


def _season_id(fixtures: list[CurrentFixture]) -> str:
    kickoffs = [datetime.fromisoformat(fixture.kickoff_time.replace("Z", "+00:00")) for fixture in fixtures if fixture.kickoff_time]
    if not kickoffs:
        raise ValueError("Cannot identify season without fixture kickoff times")
    earliest = min(kickoffs)
    start_year = earliest.year if earliest.month >= 7 else earliest.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _latest_template_states(
    root: Path, season_id: str | None = None, gameweek: int | None = None,
) -> dict[int, PlayerTemplateState]:
    try:
        from aifpl.template import TemplateCatalogStore

        return {
            row.player_id: row
            for row in TemplateCatalogStore(root).latest(season_id=season_id, gameweek=gameweek).players
        }
    except (FileNotFoundError, ValueError):
        if gameweek is not None:
            try:
                from aifpl.template import TemplateCatalogStore

                return {
                    row.player_id: row
                    for row in TemplateCatalogStore(root).latest(season_id=season_id).players
                }
            except (FileNotFoundError, ValueError):
                pass
        return {}


def _template_value(states: Mapping[int, PlayerTemplateState] | None, player_id: int, field: str) -> float | None:
    if not states or player_id not in states:
        return None
    value = getattr(states[player_id], field, None)
    return round(float(value), 4) if value is not None else None


def _template_status(states: Mapping[int, PlayerTemplateState] | None, player_id: int) -> str | None:
    if not states or player_id not in states:
        return None
    value = getattr(states[player_id], "template_status", None)
    return str(value) if value is not None else None
