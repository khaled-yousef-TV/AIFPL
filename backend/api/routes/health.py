"""
Health check and status endpoints.
"""

import os
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()

# Dependencies - will be set by main.py
betting_odds_client: Optional[object] = None


def initialize_health_router(odds_client):
    """Initialize the health router with dependencies."""
    global betting_odds_client
    betting_odds_client = odds_client


@router.get("/health")
async def health_check():
    """
    Health check endpoint.
    Can also be used to wake up the server on Render free tier.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/betting-odds-status")
async def get_betting_odds_status():
    """Debug endpoint to check betting odds configuration."""
    return {
        "enabled": betting_odds_client.enabled if betting_odds_client else False,
        "has_api_key": bool(betting_odds_client.api_key) if betting_odds_client else False,
        "weight": betting_odds_client.weight if betting_odds_client else 0,
        "api_key_set": bool(os.getenv("THE_ODDS_API_KEY")),
        "enabled_env": os.getenv("BETTING_ODDS_ENABLED", "false"),
    }


# NOTE: /wake-up endpoint remains in main.py as it depends on scheduler functions

