from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from aifpl.artifacts import complete_artifact_paths, json_bytes, jsonl_bytes, verify_artifact, write_immutable, write_manifest
from aifpl.odds import OddsSourceError, TheOddsApiClient


EVENT_MARKETS = ("team_totals", "player_assists")


@dataclass(frozen=True)
class NormalizedMarketQuote:
    event_id: str
    commence_time: str
    home_team: str
    away_team: str
    bookmaker: str
    bookmaker_last_update: str | None
    market: str
    subject: str | None
    outcome: str
    line: float | None
    decimal_odds: float
    raw_implied_probability: float
    margin_adjusted_probability: float | None


@dataclass(frozen=True)
class EventMarketCatalog:
    events_requested: int
    quotes: int
    output_path: str
    created_at: datetime
    manifest_path: str


def normalize_event_markets(payloads: list[dict]) -> list[NormalizedMarketQuote]:
    quotes: list[NormalizedMarketQuote] = []
    for event in payloads:
        try:
            event_id, commence, home, away = str(event["id"]), str(event["commence_time"]), str(event["home_team"]), str(event["away_team"])
            bookmakers = event["bookmakers"]
            if not isinstance(bookmakers, list):
                raise TypeError
        except (KeyError, TypeError) as exc:
            raise OddsSourceError("Invalid event-market response") from exc
        for bookmaker in bookmakers:
            if not isinstance(bookmaker, dict) or not isinstance(bookmaker.get("markets", []), list):
                raise OddsSourceError("Invalid bookmaker event markets")
            for market in bookmaker.get("markets", []):
                if not isinstance(market, dict) or not isinstance(market.get("outcomes", []), list):
                    raise OddsSourceError("Invalid bookmaker market outcomes")
                market_key = str(market.get("key"))
                group: list[NormalizedMarketQuote] = []
                for outcome in market["outcomes"]:
                    try:
                        price = float(outcome["price"])
                        if price <= 1:
                            raise ValueError
                        group.append(NormalizedMarketQuote(
                            event_id, commence, home, away, str(bookmaker["title"]), bookmaker.get("last_update"),
                            market_key, outcome.get("description"), str(outcome["name"]),
                            float(outcome["point"]) if outcome.get("point") is not None else None,
                            price, round(1 / price, 6), None,
                        ))
                    except (KeyError, TypeError, ValueError) as exc:
                        raise OddsSourceError("Invalid event-market outcome") from exc
                by_subject_line: dict[tuple[str | None, float | None], list[int]] = {}
                for index, quote in enumerate(group):
                    by_subject_line.setdefault((quote.subject, quote.line), []).append(index)
                for indexes in by_subject_line.values():
                    outcomes = {group[index].outcome.lower() for index in indexes}
                    if outcomes == {"over", "under"}:
                        total = sum(group[index].raw_implied_probability for index in indexes)
                        for index in indexes:
                            group[index] = replace(group[index], margin_adjusted_probability=round(group[index].raw_implied_probability / total, 6))
                quotes.extend(group)
    return quotes


class EventMarketStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def fetch(self, event_ids: list[str], client: TheOddsApiClient | None = None) -> EventMarketCatalog:
        if not event_ids:
            raise ValueError("No odds events are available for event-market fetching")
        client = client or TheOddsApiClient.from_environment()
        created_at = datetime.now(timezone.utc)
        payloads = [client.fetch_event_markets(event_id, EVENT_MARKETS)[0] for event_id in sorted(set(event_ids))]
        raw_path = self.root / "raw" / "odds" / "the_odds_api" / "epl_event_markets" / f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        write_immutable(raw_path, json_bytes({"fetched_at": created_at.isoformat(), "markets": EVENT_MARKETS, "payload": payloads}, pretty=True))
        quotes = normalize_event_markets(payloads)
        output_path = self.root / "normalized" / "odds" / "the_odds_api" / "epl_event_markets" / f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}.jsonl"
        write_immutable(output_path, jsonl_bytes(quotes))
        manifest_path = write_manifest(
            self.root, output_path, artifact_type="epl_event_markets", created_at=created_at.isoformat(),
            record_count=len(quotes), sources={"raw_event_markets": raw_path}, parameters={"markets": EVENT_MARKETS},
        )
        return EventMarketCatalog(len(set(event_ids)), len(quotes), str(output_path), created_at, str(manifest_path))

    def latest_path(self) -> Path:
        directory = self.root / "normalized" / "odds" / "the_odds_api" / "epl_event_markets"
        files = complete_artifact_paths(sorted(directory.glob("*.jsonl"))) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No event-market catalog exists; run fetch-event-markets first")
        return files[-1]

    def latest(self) -> list[NormalizedMarketQuote]:
        path = self.latest_path()
        verify_artifact(self.root, path)
        return [NormalizedMarketQuote(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]
