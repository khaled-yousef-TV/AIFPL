"""
Saved squads management endpoints (user-saved squads with custom names).
"""

import logging
from typing import Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from services.dependencies import get_dependencies

logger = logging.getLogger(__name__)

router = APIRouter()


class SaveSquadRequest(BaseModel):
    """Request model for saving a squad."""
    name: str
    squad: Dict[str, Any]  # Full squad data (formation, starting_xi, bench, captain, etc.)
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "My Favorite Squad",
                "squad": {
                    "formation": "4-4-2",
                    "starting_xi": [],
                    "bench": [],
                    "captain": 123,
                    "vice_captain": 456
                }
            }
        }


@router.get("")
async def get_saved_squads():
    """
    Get all user-saved squads (with custom names).
    Returns list of all saved squads sorted by most recently updated first.
    """
    try:
        deps = get_dependencies()
        squads = deps.db_manager.get_all_saved_squads()
        return {"squads": squads}
    except Exception as e:
        logger.error(f"Error fetching saved squads: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{name}")
async def get_saved_squad(name: str):
    """Get a specific saved squad by name."""
    try:
        deps = get_dependencies()
        squad = deps.db_manager.get_saved_squad(name)
        if not squad:
            raise HTTPException(status_code=404, detail=f"Saved squad '{name}' not found")
        return squad
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching saved squad '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def save_squad(request: SaveSquadRequest):
    """
    Save or update a squad with a custom name.
    If a squad with the same name exists, it will be updated.
    """
    try:
        deps = get_dependencies()
        name = request.name.strip() if request.name else ""
        
        # Validate squad name
        if not name:
            raise HTTPException(status_code=400, detail="Squad name is required")
        if len(name) > 200:
            raise HTTPException(status_code=400, detail="Squad name too long (max 200 characters)")
        if len(name) < 1:
            raise HTTPException(status_code=400, detail="Squad name too short")
        # Prevent XSS attempts - block HTML/script tags
        if any(char in name for char in ['<', '>', '&']):
            raise HTTPException(status_code=400, detail="Squad name contains invalid characters")
        
        if not request.squad:
            raise HTTPException(status_code=400, detail="Squad data is required")
        
        success = deps.db_manager.save_saved_squad(name, request.squad)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save squad")
        
        return {
            "success": True,
            "name": name,
            "message": f"Squad '{name}' saved successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving squad: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{name}")
async def update_saved_squad(name: str, request: SaveSquadRequest):
    """
    Update an existing saved squad.
    The name in the URL must match the name in the request body.
    """
    try:
        deps = get_dependencies()
        
        if request.name != name:
            raise HTTPException(status_code=400, detail="Name in URL must match name in request body")
        
        if not request.squad:
            raise HTTPException(status_code=400, detail="Squad data is required")
        
        # Check if exists
        existing = deps.db_manager.get_saved_squad(name)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Saved squad '{name}' not found")
        
        success = deps.db_manager.save_saved_squad(name, request.squad)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update squad")
        
        return {
            "success": True,
            "name": name,
            "message": f"Squad '{name}' updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating squad '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{name}")
async def delete_saved_squad(name: str):
    """Delete a saved squad by name."""
    try:
        deps = get_dependencies()
        success = deps.db_manager.delete_saved_squad(name)
        if not success:
            raise HTTPException(status_code=404, detail=f"Saved squad '{name}' not found")
        
        return {
            "success": True,
            "name": name,
            "message": f"Squad '{name}' deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting squad '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))

