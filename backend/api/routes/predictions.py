"""
Prediction endpoints.

Routes for player predictions, top picks, differentials, and team trends.
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException

from services.dependencies import get_dependencies
from services.prediction_service import (
    get_predictions as _get_predictions,
    get_top_picks as _get_top_picks,
    get_differentials as _get_differentials,
)
from data.trends import compute_team_trends

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/predictions")
async def get_predictions(position: Optional[int] = None, top_n: int = 100):
    """Get player predictions for next gameweek."""
    try:
        return await _get_predictions(position=position, top_n=top_n)
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/top-picks")
async def get_top_picks():
    """Get top 5 picks for each position."""
    try:
        return await _get_top_picks()
    except Exception as e:
        logger.error(f"Top picks error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/differentials")
async def get_differentials(max_ownership: float = 10.0, top_n: int = 10):
    """Get differential picks (low ownership, high predicted points)."""
    try:
        return await _get_differentials(max_ownership=max_ownership, top_n=top_n)
    except Exception as e:
        logger.error(f"Differentials error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/team-trends")
async def get_team_trends(window: int = 6, previous_window: int = 6):
    """Inspect team trend/reversal signals used by the suggester."""
    try:
        deps = get_dependencies()
        fpl_client = deps.fpl_client
        
        teams = fpl_client.get_teams()
        fixtures = fpl_client.get_fixtures(gameweek=None)
        trends = compute_team_trends(teams, fixtures, window=window, previous_window=previous_window)

        rows = sorted(trends.values(), key=lambda t: t.reversal_score, reverse=True)
        return {
            "window": window,
            "previous_window": previous_window,
            "teams": [
                {
                    "team": t.short_name,
                    "strength": t.strength,
                    "played": t.played,
                    "season_ppm": t.season_ppm,
                    "recent_ppm": t.recent_ppm,
                    "momentum": t.momentum,
                    "reversal_score": t.reversal_score,
                }
                for t in rows
            ],
        }
    except Exception as e:
        logger.error(f"Team trends error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

