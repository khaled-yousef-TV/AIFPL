"""
FPL Teams management endpoints (saved team IDs).
"""

import logging
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from services.dependencies import get_dependencies

logger = logging.getLogger(__name__)

router = APIRouter()


class SaveFplTeamRequest(BaseModel):
    """Request model for saving an FPL team."""
    team_id: int
    team_name: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "team_id": 12345,
                "team_name": "My FPL Team"
            }
        }


@router.get("")
async def get_fpl_teams():
    """
    Get all saved FPL team IDs.
    Returns list of all saved FPL teams sorted by most recently imported first.
    """
    try:
        deps = get_dependencies()
        teams = deps.db_manager.get_all_fpl_teams()
        return {"teams": teams}
    except Exception as e:
        logger.error(f"Error fetching FPL teams: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def save_fpl_team(request: SaveFplTeamRequest):
    """
    Save or update an FPL team ID.
    If a team with the same ID exists, it will be updated.
    """
    try:
        deps = get_dependencies()
        
        team_id = request.team_id
        team_name = request.team_name.strip() if request.team_name else ""
        
        if not team_id or team_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid team ID")
        if not team_name:
            raise HTTPException(status_code=400, detail="Team name is required")
        if len(team_name) > 200:
            raise HTTPException(status_code=400, detail="Team name too long (max 200 characters)")
        
        success = deps.db_manager.save_fpl_team(team_id, team_name)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save FPL team")
        
        return {
            "success": True,
            "teamId": team_id,
            "teamName": team_name,
            "message": f"FPL team ID {team_id} saved successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving FPL team: {e}")
        raise HTTPException(status_code=500, detail=str(e))

