from __future__ import annotations

import re
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayer
from aifpl.market_odds import NormalizedMarketQuote
from aifpl.odds_matching import FixtureOddsConsensus, canonical_club_name, load_team_aliases
from aifpl.artifacts import complete_artifact_paths, jsonl_bytes, verify_artifact, verify_lineage, write_immutable, write_manifest
from aifpl.current import CurrentPlayerCatalogStore
from aifpl.market_odds import EventMarketStore
from aifpl.odds_matching import FixtureOddsConsensusStore


@dataclass(frozen=True)
class TeamCleanSheetSignal:
    fixture_id: int
    team_name: str
    probability: float
    bookmaker_count: int
    methodology: str = "opponent_team_total_under_0_5_v1"


@dataclass(frozen=True)
class PlayerPropSignal:
    fixture_id: int
    player_id: int
    event_type: str
    probability: float
    bookmaker_count: int
    methodology: str = "complete_over_under_0_5_v1"


@dataclass(frozen=True)
class MarketSignalCatalog:
    clean_sheet_signals: int
    player_prop_signals: int
    output_path: str
    created_at: datetime
    manifest_path: str


def build_market_signals(
    quotes: list[NormalizedMarketQuote], consensus: list[FixtureOddsConsensus], players: list[CurrentPlayer],
    aliases: dict[str, str] | None = None,
) -> tuple[list[TeamCleanSheetSignal], list[PlayerPropSignal]]:
    fixture_by_event = {row.odds_event_id: row for row in consensus}
    clean_groups: dict[tuple[int, str], list[float]] = {}
    prop_groups: dict[tuple[int, int, str], list[float]] = {}
    player_names: dict[str, set[int]] = {}
    for player in players:
        for name in (player.name, f"{player.first_name} {player.second_name}".strip()):
            if name:
                player_names.setdefault(_name(name), set()).add(player.id)
    for quote in quotes:
        fixture = fixture_by_event.get(quote.event_id)
        if fixture is None or quote.margin_adjusted_probability is None or quote.line != 0.5:
            continue
        if quote.market == "team_totals" and quote.outcome.lower() == "under" and quote.subject:
            subject = canonical_club_name(quote.subject, aliases)
            home, away = canonical_club_name(fixture.home_team, aliases), canonical_club_name(fixture.away_team, aliases)
            if subject == away:
                clean_groups.setdefault((fixture.fixture_id, fixture.home_team), []).append(quote.margin_adjusted_probability)
            elif subject == home:
                clean_groups.setdefault((fixture.fixture_id, fixture.away_team), []).append(quote.margin_adjusted_probability)
        if quote.market == "player_assists" and quote.outcome.lower() == "over" and quote.subject:
            ids = player_names.get(_name(quote.subject), set())
            if len(ids) == 1:
                prop_groups.setdefault((fixture.fixture_id, next(iter(ids)), "assist"), []).append(quote.margin_adjusted_probability)
    clean = [TeamCleanSheetSignal(fixture, team, round(sum(values) / len(values), 6), len(values)) for (fixture, team), values in clean_groups.items()]
    props = [PlayerPropSignal(fixture, player, event_type, round(sum(values) / len(values), 6), len(values)) for (fixture, player, event_type), values in prop_groups.items()]
    return clean, props


def _name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


class MarketSignalStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build(
        self, player_catalog_path: Path | None = None, consensus_catalog_path: Path | None = None,
        market_catalog_path: Path | None = None,
    ) -> MarketSignalCatalog:
        player_store, consensus_store, market_store = CurrentPlayerCatalogStore(self.root), FixtureOddsConsensusStore(self.root), EventMarketStore(self.root)
        player_path = player_catalog_path or player_store.latest_path()
        consensus_path = consensus_catalog_path or consensus_store.latest_path()
        market_path = market_catalog_path or market_store.latest_path()
        players, consensus = player_store.load(player_path), consensus_store.load(consensus_path)
        verify_artifact(self.root, market_path)
        quotes = market_store.latest() if market_catalog_path is None else [
            NormalizedMarketQuote(**json.loads(line))
            for line in market_path.read_text(encoding="utf-8").splitlines()
        ]
        aliases, aliases_path = load_team_aliases(self.root)
        clean, props = build_market_signals(quotes, consensus, players, aliases)
        rows = [{"signal_type": "clean_sheet", **asdict(row)} for row in clean] + [{"signal_type": "player_prop", **asdict(row)} for row in props]
        created_at = datetime.now(timezone.utc)
        output_path = self.root / "normalized" / "odds" / "market_signals" / f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}.jsonl"
        write_immutable(output_path, jsonl_bytes(rows))
        sources = {"player_catalog": player_path, "fixture_consensus": consensus_path, "event_markets": market_path}
        if aliases_path is not None:
            sources["team_aliases"] = aliases_path
        manifest_path = write_manifest(
            self.root, output_path, artifact_type="market_signals", created_at=created_at.isoformat(),
            record_count=len(rows), sources=sources, parameters={"team_aliases": aliases},
        )
        return MarketSignalCatalog(len(clean), len(props), str(output_path), created_at, str(manifest_path))

    def latest_path(self) -> Path:
        directory = self.root / "normalized" / "odds" / "market_signals"
        files = complete_artifact_paths(sorted(directory.glob("*.jsonl"))) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No market signals exist; run build-market-signals first")
        return files[-1]

    def load(
        self, path: Path, player_catalog_path: Path | None = None, consensus_catalog_path: Path | None = None,
    ) -> tuple[list[TeamCleanSheetSignal], list[PlayerPropSignal]]:
        verify_artifact(self.root, path)
        expected = {}
        if player_catalog_path is not None:
            expected["player_catalog"] = player_catalog_path
        if consensus_catalog_path is not None:
            expected["fixture_consensus"] = consensus_catalog_path
        if expected:
            verify_lineage(self.root, path, expected)
        clean, props = [], []
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            signal_type = row.pop("signal_type")
            (clean if signal_type == "clean_sheet" else props).append(
                TeamCleanSheetSignal(**row) if signal_type == "clean_sheet" else PlayerPropSignal(**row)
            )
        return clean, props
