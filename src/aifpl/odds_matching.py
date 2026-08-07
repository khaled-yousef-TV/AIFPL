from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayerCatalogStore
from aifpl.fixtures import CurrentFixture, CurrentFixtureCatalogStore
from aifpl.odds import NormalizedMatchOdds, OddsSnapshotStore


CLUB_ALIASES = {
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


def canonical_club_name(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9 ]", "", name.lower()).replace("  ", " ").strip()
    return CLUB_ALIASES.get(normalized, normalized)


def match_fixture_odds(
    fixtures: list[CurrentFixture], team_names: dict[int, str], odds: list[NormalizedMatchOdds], max_kickoff_delta_seconds: int = 300
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
            if canonical_club_name(event.home_team) != canonical_club_name(home_team) or canonical_club_name(event.away_team) != canonical_club_name(away_team):
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

    def build_latest(self) -> FixtureOddsConsensusCatalog:
        players = CurrentPlayerCatalogStore(self.root).latest_players()
        team_names = {player.club_id: player.club for player in players}
        matches = match_fixture_odds(CurrentFixtureCatalogStore(self.root).latest(), team_names, OddsSnapshotStore(self.root).latest_epl_h2h())
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / "normalized" / "odds" / "fixture_consensus" / f"{run_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in matches), encoding="utf-8")
        return FixtureOddsConsensusCatalog(len(matches), str(path), created_at)

    def latest(self) -> list[FixtureOddsConsensus]:
        directory = self.root / "normalized" / "odds" / "fixture_consensus"
        files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No fixture odds consensus exists; run build-fixture-odds-consensus first")
        return [FixtureOddsConsensus(**json.loads(line)) for line in files[-1].read_text(encoding="utf-8").splitlines()]


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
