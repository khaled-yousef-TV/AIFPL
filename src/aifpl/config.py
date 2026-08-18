from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[2] / ".env")


FPL_BASE_URL = "https://fantasy.premierleague.com/api"


def cors_origins() -> list[str]:
    value = os.environ.get(
        "AIFPL_CORS_ORIGINS",
        "null,http://localhost:8000,http://127.0.0.1:8000",
    )
    origins = [origin.strip() for origin in value.split(",") if origin.strip()]
    if not origins:
        raise ValueError("AIFPL_CORS_ORIGINS must contain at least one origin")
    return origins


def data_dir() -> Path:
    """Return the configurable local location for downloaded source data."""
    return Path(os.environ.get("AIFPL_DATA_DIR", "data")).expanduser()


def freshness_hours(source: str) -> float:
    defaults = {"bootstrap": 24.0, "fixtures": 24.0, "event_live": 24.0, "odds": 6.0,
                "event_markets": 6.0, "player_evidence": 24.0}
    return float(os.environ.get(f"AIFPL_{source.upper()}_MAX_AGE_HOURS", defaults[source]))


def minimum_odds_fixture_coverage() -> float:
    coverage = float(os.environ.get("AIFPL_MIN_ODDS_FIXTURE_COVERAGE", "0.5"))
    if not 0 <= coverage <= 1:
        raise ValueError("AIFPL_MIN_ODDS_FIXTURE_COVERAGE must be within 0..1")
    return coverage


def event_market_max_events() -> int:
    value = int(os.environ.get("AIFPL_EVENT_MARKET_MAX_EVENTS", "10"))
    if not 1 <= value <= 10:
        raise ValueError("AIFPL_EVENT_MARKET_MAX_EVENTS must be within 1..10")
    return value


def minimum_bank_tenths() -> int:
    value = int(os.environ.get("AIFPL_MIN_BANK_TENTHS", "50"))
    if value < 0:
        raise ValueError("AIFPL_MIN_BANK_TENTHS must not be negative")
    return value


def bench_min_projection() -> float:
    value = float(os.environ.get("AIFPL_BENCH_MIN_PROJECTION", "2.0"))
    if value < 0:
        raise ValueError("AIFPL_BENCH_MIN_PROJECTION must not be negative")
    return value


def bench_weight() -> float:
    value = float(os.environ.get("AIFPL_BENCH_WEIGHT", "0.25"))
    if not 0 <= value <= 1:
        raise ValueError("AIFPL_BENCH_WEIGHT must be within 0..1")
    return value


def bank_shortfall_penalty() -> float:
    value = float(os.environ.get("AIFPL_BANK_SHORTFALL_PENALTY", "0.5"))
    if value < 0:
        raise ValueError("AIFPL_BANK_SHORTFALL_PENALTY must not be negative")
    return value


def dead_bench_penalty() -> float:
    value = float(os.environ.get("AIFPL_DEAD_BENCH_PENALTY", "2.0"))
    if value < 0:
        raise ValueError("AIFPL_DEAD_BENCH_PENALTY must not be negative")
    return value


def transfer_penalty() -> float:
    value = float(os.environ.get("AIFPL_TRANSFER_PENALTY", "1.0"))
    if value < 0:
        raise ValueError("AIFPL_TRANSFER_PENALTY must not be negative")
    return value


def partial_odds_fixture_coverage() -> float:
    coverage = float(os.environ.get("AIFPL_PARTIAL_ODDS_FIXTURE_COVERAGE", "0.8"))
    if not 0 <= coverage <= 1:
        raise ValueError("AIFPL_PARTIAL_ODDS_FIXTURE_COVERAGE must be within 0..1")
    if coverage < minimum_odds_fixture_coverage():
        raise ValueError("AIFPL_PARTIAL_ODDS_FIXTURE_COVERAGE must be at least AIFPL_MIN_ODDS_FIXTURE_COVERAGE")
    return coverage


@dataclass(frozen=True)
class HttpRetrySettings:
    attempts: int
    base_delay_seconds: float


def http_retry_settings() -> HttpRetrySettings:
    attempts = int(os.environ.get("AIFPL_HTTP_RETRY_ATTEMPTS", "3"))
    base_delay = float(os.environ.get("AIFPL_HTTP_RETRY_BASE_SECONDS", "0.5"))
    if attempts < 1:
        raise ValueError("AIFPL_HTTP_RETRY_ATTEMPTS must be at least 1")
    if base_delay < 0:
        raise ValueError("AIFPL_HTTP_RETRY_BASE_SECONDS must not be negative")
    return HttpRetrySettings(attempts=attempts, base_delay_seconds=base_delay)


@dataclass(frozen=True)
class SchedulerSettings:
    lead_minutes: int
    horizon_gameweeks: int
    poll_seconds: float
    budget: int


def scheduler_settings() -> SchedulerSettings:
    settings = SchedulerSettings(
        lead_minutes=int(os.environ.get("AIFPL_SCHEDULER_LEAD_MINUTES", "90")),
        horizon_gameweeks=int(os.environ.get("AIFPL_SCHEDULER_HORIZON_GAMEWEEKS", "6")),
        poll_seconds=float(os.environ.get("AIFPL_SCHEDULER_POLL_SECONDS", "300")),
        budget=int(os.environ.get("AIFPL_SCHEDULER_BUDGET", "1000")),
    )
    if settings.lead_minutes < 0:
        raise ValueError("AIFPL_SCHEDULER_LEAD_MINUTES must not be negative")
    if not 1 <= settings.horizon_gameweeks <= 38:
        raise ValueError("AIFPL_SCHEDULER_HORIZON_GAMEWEEKS must be within 1..38")
    if settings.poll_seconds <= 0:
        raise ValueError("AIFPL_SCHEDULER_POLL_SECONDS must be positive")
    if settings.budget < 0:
        raise ValueError("AIFPL_SCHEDULER_BUDGET must not be negative")
    return settings


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    chat_id: str
    enabled: bool
    notify_lead_minutes: int


def telegram_settings() -> TelegramSettings:
    settings = TelegramSettings(
        bot_token=os.environ.get("AIFPL_TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.environ.get("AIFPL_TELEGRAM_CHAT_ID", ""),
        enabled=os.environ.get("AIFPL_TELEGRAM_ENABLED", "false").lower() in ("1", "true", "yes"),
        notify_lead_minutes=int(os.environ.get("AIFPL_TELEGRAM_NOTIFY_LEAD_MINUTES", "240")),
    )
    if settings.enabled and (not settings.bot_token or not settings.chat_id):
        raise ValueError("AIFPL_TELEGRAM_BOT_TOKEN and AIFPL_TELEGRAM_CHAT_ID are required when telegram is enabled")
    if settings.notify_lead_minutes < 0:
        raise ValueError("AIFPL_TELEGRAM_NOTIFY_LEAD_MINUTES must not be negative")
    return settings


@dataclass(frozen=True)
class HermesSettings:
    base_url: str
    model: str
    api_key: str
    max_tool_steps: int
    timeout_seconds: float


def hermes_settings() -> HermesSettings:
    settings = HermesSettings(
        base_url=os.environ.get("HERMES_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        model=os.environ.get("HERMES_MODEL", "deepseek-v4-flash"),
        api_key=os.environ.get("HERMES_API_KEY", ""),
        max_tool_steps=int(os.environ.get("HERMES_MAX_TOOL_STEPS", "8")),
        timeout_seconds=float(os.environ.get("HERMES_TIMEOUT_SECONDS", "120")),
    )
    if not settings.api_key:
        raise ValueError("HERMES_API_KEY is required")
    if settings.max_tool_steps < 1:
        raise ValueError("HERMES_MAX_TOOL_STEPS must be positive")
    if settings.timeout_seconds <= 0:
        raise ValueError("HERMES_TIMEOUT_SECONDS must be positive")
    return settings
