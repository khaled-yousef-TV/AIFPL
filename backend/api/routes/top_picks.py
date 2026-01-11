"""
Top picks and differentials endpoints.
"""

import logging
from fastapi import APIRouter, HTTPException

from services.dependencies import get_dependencies
from data.trends import compute_team_trends

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/top-picks")
async def get_top_picks():
    """Get top 5 picks for each position."""
    # Import here to avoid circular imports - this calls the predictions endpoint
    from api.main import get_predictions
    
    try:
        result = {}
        for pos_id, pos_name in [(1, "goalkeepers"), (2, "defenders"), (3, "midfielders"), (4, "forwards")]:
            preds = await get_predictions(position=pos_id, top_n=5)
            result[pos_name] = preds["predictions"]
        return result
    except Exception as e:
        logger.error(f"Top picks error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/differentials")
async def get_differentials(max_ownership: float = 10.0, top_n: int = 10):
    """Get differential picks (low ownership, high predicted points)."""
    from api.main import get_predictions
    
    try:
        preds = await get_predictions(top_n=500)
        differentials = [
            p for p in preds["predictions"]
            if p["ownership"] < max_ownership and p["predicted_points"] >= 4.0
        ]
        differentials.sort(key=lambda x: x["predicted_points"], reverse=True)
        return {"differentials": differentials[:top_n]}
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

        # Sort by reversal_score desc
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

