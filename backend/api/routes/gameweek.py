"""
Gameweek information endpoints.
"""

import logging
from fastapi import APIRouter, HTTPException

from services.dependencies import get_dependencies

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/gameweek")
async def get_gameweek():
    """Get current and next gameweek info."""
    try:
        deps = get_dependencies()
        current = deps.fpl_client.get_current_gameweek()
        next_gw = deps.fpl_client.get_next_gameweek()
        
        return {
            "current": {
                "id": current.id if current else None,
                "name": current.name if current else None,
                "finished": current.finished if current else None
            } if current else None,
            "next": {
                "id": next_gw.id if next_gw else None,
                "name": next_gw.name if next_gw else None,
                "deadline": next_gw.deadline_time.isoformat() if next_gw and next_gw.deadline_time else None
            } if next_gw else None
        }
    except Exception as e:
        logger.error(f"Error getting gameweek: {e}")
        raise HTTPException(status_code=500, detail=str(e))

