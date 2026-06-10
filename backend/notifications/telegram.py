"""
Telegram notifications.

Thin Bot API wrapper (no SDK) + message formatting for the pre-deadline
suggested-squad message. Disabled cleanly when TELEGRAM_* env vars are
unset.
"""

import logging
import os
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Send messages via the Telegram Bot API."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        enabled_str = os.getenv("TELEGRAM_ENABLED", "false")
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = (
            enabled_str.lower() == "true" and bool(self.bot_token) and bool(self.chat_id)
        )
        if enabled_str.lower() == "true" and not self.enabled:
            logger.warning(
                "TELEGRAM_ENABLED=true but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing — disabled"
            )

    def send(self, text: str) -> bool:
        """
        Send a message, preferring Markdown but never losing it to a parse error.

        Legacy Markdown is strict: unbalanced `*`/`_`/`[` in LLM-generated
        narrative or player names (e.g. "O'Brien") makes Telegram reject the
        whole message with HTTP 400. So we try Markdown once and, on a 4xx,
        retry the same content as plain text rather than dropping the alert.
        """
        if not self.enabled:
            logger.debug("Telegram disabled — message not sent")
            return False

        body = text[:4096]  # Telegram hard limit
        if self._post(body, parse_mode="Markdown"):
            return True
        # Markdown was rejected (or another transient error) — fall back to
        # plain text so the user still gets the message.
        logger.warning("Telegram Markdown send failed — retrying as plain text")
        return self._post(body, parse_mode=None)

    def _post(self, text: str, parse_mode: Optional[str]) -> bool:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram send failed (parse_mode={parse_mode}): {e}")
            return False


def format_squad_message(
    squad_data: Dict,
    gameweek: int,
    hermes_narrative: Optional[str] = None,
    transfer_lines: Optional[List[str]] = None,
) -> str:
    """
    Format the pre-deadline suggested-squad message.

    squad_data is the assemble_squad_result/build_squad_with_predictor dict
    (starting_xi, bench, captain, vice_captain, formation, ...).
    """
    lines = [f"⚽ *FPL Deadline in 1 hour — GW{gameweek}*", ""]

    formation = squad_data.get("formation", "?")
    lines.append(f"*Suggested XI* ({formation}):")

    by_position: Dict[str, List[str]] = {}
    for p in squad_data.get("starting_xi", []):
        pos = p.get("position", "?")
        marker = ""
        if p.get("is_captain"):
            marker = " (C)"
        elif p.get("is_vice_captain"):
            marker = " (V)"
        by_position.setdefault(pos, []).append(f"{p.get('name', '?')}{marker}")

    for pos in ("GK", "DEF", "MID", "FWD"):
        if pos in by_position:
            lines.append(f"  {pos}: {', '.join(by_position[pos])}")

    bench = squad_data.get("bench", [])
    if bench:
        lines.append(f"*Bench*: {', '.join(p.get('name', '?') for p in bench)}")

    captain = squad_data.get("captain", {})
    if captain:
        lines.append(f"*Captain*: {captain.get('name', '?')} ({captain.get('predicted', '?')} xPts)")

    predicted = squad_data.get("predicted_points")
    if predicted is not None:
        lines.append(f"*Projected points*: {predicted}")

    if transfer_lines:
        lines.append("")
        lines.append("*Transfer suggestions*:")
        lines.extend(f"  • {t}" for t in transfer_lines[:3])

    if hermes_narrative:
        lines.append("")
        lines.append("*Hermes says*:")
        lines.append(hermes_narrative[:800])

    return "\n".join(lines)
