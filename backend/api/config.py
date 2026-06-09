"""
Configuration and Environment Variable Validation

Validates required environment variables and provides configuration helpers.
"""

import os
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)


def validate_env() -> None:
    """
    Validate that required environment variables are set.
    Raises ValueError if any required variables are missing.
    """
    required_vars: List[str] = []  # Add any required vars here in the future
    
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    
    # Validate optional but important vars
    optional_vars = {
        "THE_ODDS_API_KEY": "Betting odds integration",
        "DATABASE_URL": "Database connection",
        "CORS_ORIGINS": "CORS configuration",
        # Hermes LLM orchestrator (any OpenAI-compatible provider)
        "LLM_BASE_URL": "Hermes LLM endpoint (Nous/OpenRouter/DeepSeek)",
        "LLM_MODEL": "Hermes LLM model id",
        "LLM_API_KEY": "Hermes LLM API key",
        # News agent web search (Phase 3)
        "TAVILY_API_KEY": "News agent web search",
        # Telegram notifications (Phase 3)
        "TELEGRAM_BOT_TOKEN": "Telegram pre-deadline notifications",
        "TELEGRAM_CHAT_ID": "Telegram chat target",
    }
    
    for var, description in optional_vars.items():
        if os.getenv(var):
            logger.info(f"✓ {var} is set ({description})")
        else:
            logger.debug(f"⚠ {var} not set ({description} - optional)")


def get_log_level() -> str:
    """Get log level from environment or default to INFO."""
    return os.getenv("LOG_LEVEL", "INFO").upper()


def setup_logging() -> None:
    """Configure structured logging."""
    log_level = get_log_level()
    
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    logger.info(f"Logging configured at {log_level} level")

