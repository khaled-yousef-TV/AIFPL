from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayerCatalogStore
from aifpl.fixtures import CurrentFixture, CurrentFixtureCatalogStore
from aifpl.odds import NormalizedMatchOdds, OddsSnapshotStore
from aifpl.artifacts import complete_artifact_paths, jsonl_bytes, verify_artifact, write_immutable, write_manifest


DEFAULT_CLUB_ALIASES = {
    "brighton and hove albion": "brighton",
    "leeds united": "leeds",
    "manchester city": "man city",
    "manchester united": "man utd",
    "newcastle united": "newcastle",
    "nottingham forest": "nottm forest",
    "nottm forest": "nottm forest",
    "tottenham hotspur": "spurs",
}


@dataclass(frozen=True)
class FixtureOddsConsensus:
    fixture_id: int
    gameweek: int | None
    odds_event_id: str
    home_team: str
    away_team: str
    commence_time: str
    kickoff_delta_seconds: float
    bookmakers: int
    home_win_probability: float
    draw_probability: float
    away_win_probability: float


@dataclass(frozen=True)
class FixtureOddsConsensusCatalog:
    matches: int
    output_path: str
    created_at: datetime
    source_player_catalog: str | None = None
    source_fixture_catalog: str | None = None
    source_odds_catalog: str | None = None
    manifest_path: str | None = None
    team_aliases_path: str | None = None


def _normalize_club_name(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", "", name.lower()).split())


def team_aliases_path(root: Path) -> Path:
    configured = os.environ.get("AIFPL_TEAM_ALIASES_FILE")
    return Path(configured).expanduser() if configured else root / "config" / "team_aliases.json"


def load_team_aliases(root: Path) -> tuple[dict[str, str], Path | None]:
    aliases = dict(DEFAULT_CLUB_ALIASES)
    path = team_aliases_path(root)
    if not path.exists():
        return aliases, None
    try:
        configured = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load team aliases from {path}: {exc}") from exc
    if not isinstance(configured, dict):
        raise ValueError("Team alias configuration must be a JSON object")
    for source, target in configured.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("Every team alias and canonical name must be a string")
        normalized_source = _normalize_club_name(source)
        normalized_target = _normalize_club_name(target)
        if not normalized_source or not normalized_target:
            raise ValueError("Team aliases and canonical names must not be empty")
        aliases[normalized_source] = aliases.get(normalized_target, normalized_target)
    return aliases, path


def canonical_club_name(name: str, aliases: dict[str, str] | None = None) -> str:
    normalized = _normalize_club_name(name)
    return (aliases or DEFAULT_CLUB_ALIASES).get(normalized, normalized)


def match_fixture_odds(
    fixtures: list[CurrentFixture], team_names: dict[int, str], odds: list[NormalizedMatchOdds],
    max_kickoff_delta_seconds: int = 300, aliases: dict[str, str] | None = None,
) -> list[FixtureOddsConsensus]:
    grouped: dict[str, list[NormalizedMatchOdds]] = {}
    for row in odds:
        grouped.setdefault(row.event_id, []).append(row)
    consensus: list[FixtureOddsConsensus] = []
    for fixture in fixtures:
        if fixture.kickoff_time is None:
            continue
        home_team = team_names.get(fixture.home_team_id)
        away_team = team_names.get(fixture.away_team_id)
        if home_team is None or away_team is None:
            continue
        fixture_time = _parse_timestamp(fixture.kickoff_time)
        matches: list[tuple[list[NormalizedMatchOdds], float]] = []
        for event_rows in grouped.values():
            event = event_rows[0]
            if canonical_club_name(event.home_team, aliases) != canonical_club_name(home_team, aliases) or canonical_club_name(event.away_team, aliases) != canonical_club_name(away_team, aliases):
                continue
            delta = abs((_parse_timestamp(event.commence_time) - fixture_time).total_seconds())
            if delta <= max_kickoff_delta_seconds:
                matches.append((event_rows, delta))
        if len(matches) != 1:
            continue
        event_rows, delta = matches[0]
        first = event_rows[0]
        consensus.append(FixtureOddsConsensus(
            fixture_id=fixture.id,
            gameweek=fixture.gameweek,
            odds_event_id=first.event_id,
            home_team=home_team,
            away_team=away_team,
            commence_time=first.commence_time,
            kickoff_delta_seconds=delta,
            bookmakers=len(event_rows),
            home_win_probability=round(sum(row.home_win_probability for row in event_rows) / len(event_rows), 6),
            draw_probability=round(sum(row.draw_probability for row in event_rows) / len(event_rows), 6),
            away_win_probability=round(sum(row.away_win_probability for row in event_rows) / len(event_rows), 6),
        ))
    return sorted(consensus, key=lambda item: item.fixture_id)


class FixtureOddsConsensusStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build_latest(
        self, player_catalog_path: Path | None = None, fixture_catalog_path: Path | None = None,
        odds_catalog_path: Path | None = None,
    ) -> FixtureOddsConsensusCatalog:
        player_store = CurrentPlayerCatalogStore(self.root)
        fixture_store = CurrentFixtureCatalogStore(self.root)
        player_path = player_catalog_path or player_store.latest_path()
        fixture_path = fixture_catalog_path or fixture_store.latest_path()
        players = player_store.load(player_path)
        fixtures = fixture_store.load(fixture_path)
        odds_store = OddsSnapshotStore(self.root)
        odds_path = odds_catalog_path or odds_store.latest_path()
        aliases, aliases_path = load_team_aliases(self.root)
        team_names = {player.club_id: player.club for player in players}
        matches = match_fixture_odds(fixtures, team_names, odds_store.load(odds_path), aliases=aliases)
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / "normalized" / "odds" / "fixture_consensus" / f"{run_id}.jsonl"
        write_immutable(path, jsonl_bytes(matches))
        sources = {"player_catalog": player_path, "fixture_catalog": fixture_path, "normalized_odds": odds_path}
        if aliases_path is not None:
            sources["team_aliases"] = aliases_path
        manifest_path = write_manifest(
            self.root, path, artifact_type="fixture_odds_consensus", created_at=created_at.isoformat(),
            record_count=len(matches),
            sources=sources,
            parameters={"max_kickoff_delta_seconds": 300, "team_aliases": aliases},
        )
        return FixtureOddsConsensusCatalog(
            len(matches), str(path), created_at, str(player_path), str(fixture_path), str(odds_path),
            str(manifest_path), str(aliases_path) if aliases_path is not None else None,
        )

    def latest(self) -> list[FixtureOddsConsensus]:
        return self.load(self.latest_path())

    def latest_path(self) -> Path:
        directory = self.root / "normalized" / "odds" / "fixture_consensus"
        files = complete_artifact_paths(sorted(directory.glob("*.jsonl"))) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No fixture odds consensus exists; run build-fixture-odds-consensus first")
        return files[-1]

    def load(self, path: Path) -> list[FixtureOddsConsensus]:
        verify_artifact(self.root, path)
        return [FixtureOddsConsensus(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
