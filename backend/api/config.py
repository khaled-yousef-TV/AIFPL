"""
Configuration and Environment Variable Validation

Validates required environment variables and provides configuration helpers.
"""

import os
import logging
from typing import Optional

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

