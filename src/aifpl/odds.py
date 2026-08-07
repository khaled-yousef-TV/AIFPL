from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
EPL_SPORT_KEY = "soccer_epl"


class OddsSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedMatchOdds:
    event_id: str
    commence_time: str
    home_team: str
    away_team: str
    bookmaker: str
    bookmaker_last_update: str | None
    home_win_probability: float
    draw_probability: float
    away_win_probability: float


@dataclass(frozen=True)
class OddsSnapshotSummary:
    fetched_at: datetime
    events: int
    bookmaker_markets: int
    requests_remaining: str | None
    requests_used: str | None
    requests_last: str | None
    raw_path: str
    normalized_path: str


class TheOddsApiClient:
    def __init__(self, api_key: str, base_url: str = ODDS_API_BASE_URL, timeout_seconds: float = 20.0) -> None:
        if not api_key:
            raise OddsSourceError("ODDS_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "TheOddsApiClient":
        return cls(os.environ.get("ODDS_API_KEY", ""))

    def fetch_epl_h2h(self) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
        try:
            response = httpx.get(
                f"{self.base_url}/sports/{EPL_SPORT_KEY}/odds/",
                params={"apiKey": self.api_key, "regions": "uk", "markets": "h2h", "oddsFormat": "decimal", "dateFormat": "iso"},
                timeout=self.timeout_seconds,
                headers={"User-Agent": "aifpl-backtester/0.1"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OddsSourceError(f"Could not fetch EPL odds: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise OddsSourceError("Odds provider did not return valid JSON") from exc
        if not isinstance(payload, list):
            raise OddsSourceError("Odds provider returned an invalid event collection")
        headers = {key: response.headers.get(key) for key in ("x-requests-remaining", "x-requests-used", "x-requests-last")}
        return payload, headers


class OddsSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save_epl_h2h(self, payload: list[dict[str, Any]], headers: dict[str, str | None], fetched_at: datetime | None = None) -> OddsSnapshotSummary:
        retrieved = fetched_at or datetime.now(timezone.utc)
        if retrieved.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        normalized = normalize_epl_h2h(payload)
        stamp = retrieved.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        raw_path = self.root / "raw" / "odds" / "the_odds_api" / "epl" / f"{stamp}.json"
        normalized_path = self.root / "normalized" / "odds" / "the_odds_api" / "epl" / f"{stamp}.jsonl"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps({"metadata": {"source": "the-odds-api:epl-h2h", "fetched_at": retrieved.isoformat(), "quota": headers}, "payload": payload}, separators=(",", ":")), encoding="utf-8")
        normalized_path.write_text("".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in normalized), encoding="utf-8")
        return OddsSnapshotSummary(retrieved, len(payload), len(normalized), headers.get("x-requests-remaining"), headers.get("x-requests-used"), headers.get("x-requests-last"), str(raw_path), str(normalized_path))

    def latest_epl_h2h(self) -> list[NormalizedMatchOdds]:
        directory = self.root / "normalized" / "odds" / "the_odds_api" / "epl"
        files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No normalized EPL odds exist; run fetch-epl-odds first")
        return [NormalizedMatchOdds(**json.loads(line)) for line in files[-1].read_text(encoding="utf-8").splitlines()]


def normalize_epl_h2h(payload: list[dict[str, Any]]) -> list[NormalizedMatchOdds]:
    rows: list[NormalizedMatchOdds] = []
    for event_index, event in enumerate(payload):
        try:
            event_id, home_team, away_team, commence_time = str(event["id"]), str(event["home_team"]), str(event["away_team"]), str(event["commence_time"])
            bookmakers = event["bookmakers"]
            if not isinstance(bookmakers, list):
                raise TypeError("bookmakers")
        except (KeyError, TypeError, ValueError) as exc:
            raise OddsSourceError(f"Invalid event {event_index} in odds response") from exc
        for bookmaker in bookmakers:
            h2h = next((market for market in bookmaker.get("markets", []) if market.get("key") == "h2h"), None)
            if not isinstance(h2h, dict):
                continue
            outcomes = {outcome.get("name"): outcome.get("price") for outcome in h2h.get("outcomes", []) if isinstance(outcome, dict)}
            try:
                prices = {name: float(outcomes[name]) for name in (home_team, "Draw", away_team)}
                if any(price <= 1 for price in prices.values()):
                    raise ValueError("decimal odds must exceed 1")
                raw = {name: 1 / price for name, price in prices.items()}
                total = sum(raw.values())
                rows.append(NormalizedMatchOdds(event_id, commence_time, home_team, away_team, str(bookmaker["title"]), bookmaker.get("last_update"), round(raw[home_team] / total, 6), round(raw["Draw"] / total, 6), round(raw[away_team] / total, 6)))
            except (KeyError, TypeError, ValueError):
                continue
    return rows
