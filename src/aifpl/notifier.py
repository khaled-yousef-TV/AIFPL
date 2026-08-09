from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from aifpl.config import http_retry_settings, telegram_settings
from aifpl.current import CurrentPlayerCatalogStore
from aifpl.hermes import HermesDecision, HermesManager
from aifpl.odds_projections import OddsProjectionStore
from aifpl.retry import retry_sync
from aifpl.security import redact_secrets

TELEGRAM_API = "https://api.telegram.org"
CHIP_METHODOLOGY = "provisional_chip_rules_v1"


class TelegramNotifierError(RuntimeError):
    """Raised when the Telegram Bot API rejects or cannot reach a message."""


@dataclass(frozen=True)
class ChipAdvice:
    chip: str
    rationale: str
    methodology: str = CHIP_METHODOLOGY


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout_seconds: float = 20.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    @classmethod
    def from_environment(cls) -> "TelegramNotifier":
        settings = telegram_settings()
        if not settings.enabled:
            raise TelegramNotifierError("Telegram notifications are disabled; set AIFPL_TELEGRAM_ENABLED=true")
        return cls(settings.bot_token, settings.chat_id)

    def send_message(self, text: str) -> None:
        url = f"{TELEGRAM_API}/bot{self.bot_token}/sendMessage"

        def request() -> httpx.Response:
            kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            with httpx.Client(**kwargs) as client:
                response = client.post(url, json={"chat_id": self.chat_id, "text": text})
                response.raise_for_status()
                return response

        try:
            response = retry_sync(request, http_retry_settings())
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramNotifierError(redact_secrets(f"Telegram message failed: {type(exc).__name__}")) from exc
        if not payload.get("ok"):
            raise TelegramNotifierError(redact_secrets(f"Telegram rejected the message: {payload.get('description')}"))


def recommend_chip(decision: HermesDecision, bench_projected_points: float) -> ChipAdvice | None:
    transfers_made = len(decision.transfers_in)
    if decision.action == "execute_horizon" and transfers_made >= 3:
        return ChipAdvice(
            chip="wildcard",
            rationale=f"The plan makes {transfers_made} transfers; consider a wildcard before the deadline",
        )
    if bench_projected_points >= 12:
        return ChipAdvice(
            chip="bench_boost",
            rationale=f"Bench projects to {bench_projected_points:.1f} points; a bench boost is worth considering",
        )
    return None


def build_recommendation_message(root: Path, event: int, season_id: str, deadline: datetime) -> str:
    manager = HermesManager(root)
    try:
        decision = manager.latest_decision()
    except FileNotFoundError:
        return _no_decision_message(event, season_id, deadline)
    names = _player_names(root)
    deadline_utc = deadline.astimezone(timezone.utc)
    lines = [
        f"GW {event} | {season_id}",
        f"Deadline: {deadline_utc.strftime('%a %d %b %Y, %H:%M UTC')}",
        "-------------------------------------",
        f"Hermes: {decision.action} ({decision.model})",
        f"Captain: {_describe(decision.captain_id, names)} (C)",
    ]
    if decision.action == "adopt_initial":
        lines.append("Transfers: new squad adopted (pre-season unlimited transfers)")
    elif decision.transfers_in or decision.transfers_out:
        transfers = ", ".join(
            f"{_describe(out, names)} -> {_describe(inn, names)}"
            for out, inn in zip(decision.transfers_out, decision.transfers_in)
        )
        lines.append(f"Transfers ({len(decision.transfers_in)}): {transfers}")
    else:
        lines.append("Transfers: none (hold)")
    bench_ids = [element for element in decision.squad.player_ids if element not in set(decision.starting_xi_ids)]
    bench_points = _bench_projected_points(root, event, bench_ids)
    chip = recommend_chip(decision, bench_points)
    if chip is None:
        lines.append("Chip: none recommended")
    else:
        lines.append(f"Chip: {chip.chip} - {chip.rationale}")
    lines.append(f"Bank: {decision.squad.bank / 10:.1f}m | Free transfers: {decision.squad.free_transfers}")
    lines.append("-------------------------------------")
    lines.extend(_lineup_section(decision.starting_xi_ids, names))
    lines.append("-------------------------------------")
    lines.append(f"Bench: {', '.join(_describe(element, names) for element in bench_ids)}")
    return "\n".join(lines)


def build_scorecard_message(root: Path) -> str:
    from aifpl.scoring import DecisionScorer

    record = DecisionScorer(root).latest()
    delta = record.total_actual - record.total_projected
    lines = [
        f"GW {record.gameweek} scorecard ({record.season_id})",
        "-------------------------------------",
        f"Projected: {record.total_projected:.1f} | Actual: {record.total_actual:.1f} ({delta:+.1f})",
        f"Starting XI + captain: {record.xi_projected:.1f} projected, {record.xi_actual:.1f} actual",
        f"Bench: {record.bench_projected:.1f} projected, {record.bench_actual:.1f} actual",
    ]
    if record.captain is not None:
        captain = record.captain
        lines.append(
            f"Captain: {captain.name} - {captain.projected:.1f} projected, {captain.actual:.1f} actual"
        )
    if record.transfers:
        for transfer in record.transfers:
            lines.append(f"Transfer: {transfer.out_name} -> {transfer.in_name} ({transfer.delta:+.1f} pts)")
    else:
        lines.append("Transfers: none (hold)")
    lines.append("-------------------------------------")
    best = sorted(record.players, key=lambda player: player.actual, reverse=True)[:3]
    lines.append("Best performers: " + ", ".join(f"{player.name} ({player.actual})" for player in best))
    worst = sorted(record.players, key=lambda player: player.actual)[:2]
    lines.append("Worst: " + ", ".join(f"{player.name} ({player.actual})" for player in worst))
    return "\n".join(lines)


def _no_decision_message(event: int, season_id: str, deadline: datetime) -> str:
    deadline_utc = deadline.astimezone(timezone.utc)
    return (
        f"GW {event} | {season_id}\n"
        f"Deadline: {deadline_utc.strftime('%a %d %b %Y, %H:%M UTC')}\n"
        "-------------------------------------\n"
        "No Hermes decision exists yet for this gameweek."
    )


def _lineup_section(starting_ids: list[int], names: dict[int, tuple[str, str, str]]) -> list[str]:
    grouped = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for element in starting_ids:
        position = names.get(element, (None, None, "?") )[2]
        grouped.setdefault(position, []).append(element)
    formation = "-".join(str(len(grouped[position])) for position in ("GK", "DEF", "MID", "FWD"))
    lines = [f"Starting XI ({formation})"]
    for position in ("GK", "DEF", "MID", "FWD"):
        if grouped[position]:
            players = ", ".join(_describe(element, names) for element in grouped[position])
            lines.append(f"{position}: {players}")
    return lines


def _player_names(root: Path) -> dict[int, tuple[str, str, str]]:
    try:
        players = CurrentPlayerCatalogStore(root).latest_players()
    except FileNotFoundError:
        return {}
    return {player.id: (player.name, player.club, player.position) for player in players}


def _describe(element: int, names: dict[int, tuple[str, str, str]]) -> str:
    if element not in names:
        return f"#{element}"
    name, club, _ = names[element]
    return f"{name} ({club})"


def _bench_projected_points(root: Path, gameweek: int, bench_ids: list[int]) -> float:
    if not bench_ids:
        return 0.0
    directory = root / "normalized" / "current" / "odds_projections"
    candidates: list[tuple[datetime, Path]] = []
    for path in directory.glob("*.jsonl"):
        manifest = path.with_suffix(".manifest.json")
        if manifest.exists():
            candidates.append((datetime.fromisoformat(json.loads(manifest.read_text(encoding="utf-8"))["created_at"]), path))
    for _, path in sorted(candidates, reverse=True):
        try:
            rows = OddsProjectionStore(root).latest(path.name)
        except ValueError:
            continue
        if gameweek not in {row.gameweek for row in rows}:
            continue
        return round(sum(
            row.projected_points for row in rows
            if row.gameweek == gameweek and row.player_id in set(bench_ids)
        ), 2)
    return 0.0
