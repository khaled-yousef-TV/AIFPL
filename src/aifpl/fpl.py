from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

from aifpl.config import FPL_BASE_URL


class FplSourceError(RuntimeError):
    """Raised when the official public FPL source cannot be used safely."""


class BootstrapSummary(BaseModel):
    fetched_at: datetime
    players: int = Field(ge=0)
    teams: int = Field(ge=0)
    events: int = Field(ge=0)
    current_event: int | None = None
    next_event: int | None = None


class FixtureSummary(BaseModel):
    fetched_at: datetime
    fixtures: int = Field(ge=0)
    finished: int = Field(ge=0)
    gameweeks: list[int] = Field(default_factory=list)


class EventLiveSummary(BaseModel):
    fetched_at: datetime
    event: int = Field(ge=1)
    players: int = Field(ge=0)
    total_points: int = Field(ge=0)


class FplClient:
    def __init__(self, base_url: str = FPL_BASE_URL, timeout_seconds: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def fetch_bootstrap(self) -> dict[str, Any]:
        return await self._fetch_json("/bootstrap-static/")

    async def fetch_fixtures(self) -> list[dict[str, Any]]:
        payload = await self._fetch_json("/fixtures/")
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise FplSourceError("FPL fixtures response has invalid collection type")
        return payload

    async def fetch_event_live(self, event: int) -> dict[str, Any]:
        if event < 1:
            raise ValueError("event must be at least 1")
        payload = await self._fetch_json(f"/event/{event}/live/")
        if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
            raise FplSourceError("FPL event live response is missing player elements")
        return payload

    async def _fetch_json(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url, headers={"User-Agent": "aifpl-backtester/0.1"})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FplSourceError(f"Could not fetch FPL data at {path}: {exc}") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise FplSourceError("FPL source did not return valid JSON") from exc


def validate_bootstrap(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"elements", "teams", "events"}
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise FplSourceError("FPL bootstrap response is missing expected collections")
    if not all(isinstance(payload[key], list) for key in required):
        raise FplSourceError("FPL bootstrap response has invalid collection types")
    return payload


def summarize_bootstrap(payload: dict[str, Any], fetched_at: datetime | None = None) -> BootstrapSummary:
    validate_bootstrap(payload)
    events = payload["events"]
    current = next((event["id"] for event in events if event.get("is_current")), None)
    next_event = next((event["id"] for event in events if event.get("is_next")), None)
    return BootstrapSummary(
        fetched_at=fetched_at or datetime.now(timezone.utc),
        players=len(payload["elements"]),
        teams=len(payload["teams"]),
        events=len(events),
        current_event=current,
        next_event=next_event,
    )


def summarize_fixtures(payload: list[dict[str, Any]], fetched_at: datetime | None = None) -> FixtureSummary:
    gameweeks = sorted({fixture["event"] for fixture in payload if isinstance(fixture.get("event"), int)})
    return FixtureSummary(
        fetched_at=fetched_at or datetime.now(timezone.utc),
        fixtures=len(payload),
        finished=sum(bool(fixture.get("finished")) for fixture in payload),
        gameweeks=gameweeks,
    )


def summarize_event_live(
    event: int, payload: dict[str, Any], fetched_at: datetime | None = None
) -> EventLiveSummary:
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise FplSourceError("FPL event live response is missing player elements")
    total_points = sum(
        item.get("stats", {}).get("total_points", 0)
        for item in elements
        if isinstance(item, dict) and isinstance(item.get("stats"), dict)
    )
    return EventLiveSummary(
        fetched_at=fetched_at or datetime.now(timezone.utc),
        event=event,
        players=len(elements),
        total_points=total_points,
    )
