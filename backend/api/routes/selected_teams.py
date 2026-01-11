"""
Selected teams (daily snapshots) endpoints.
"""

import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks

from services.dependencies import get_dependencies

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_selected_teams():
    """
    Get all saved teams for all gameweeks.
    Returns daily snapshot for current/next gameweek, final team for past gameweeks.
    """
    try:
        deps = get_dependencies()
        fpl_client = deps.fpl_client
        db_manager = deps.db_manager
        
        # Get current/next gameweek
        next_gw = fpl_client.get_next_gameweek()
        current_gw_id = next_gw.id if next_gw else None
        
        # Get all final teams (30 min before deadline)
        final_teams = db_manager.get_all_selected_teams()
        
        # Build response: use daily snapshot for current gameweek, final team for past
        teams_result = []
        processed_gameweeks = set()
        
        # Process all final teams
        for team in final_teams:
            gw = team["gameweek"]
            processed_gameweeks.add(gw)
            
            # For current/next gameweek, prefer daily snapshot
            if current_gw_id and gw >= current_gw_id:
                daily_snapshot = db_manager.get_latest_daily_snapshot(gw)
                if daily_snapshot:
                    teams_result.append({
                        **daily_snapshot,
                        "type": "daily_snapshot"
                    })
                else:
                    teams_result.append({
                        **team,
                        "type": "final"
                    })
            else:
                teams_result.append({
                    **team,
                    "type": "final"
                })
        
        # If current gameweek has no final team but might have daily snapshot
        if current_gw_id and current_gw_id not in processed_gameweeks:
            daily_snapshot = db_manager.get_latest_daily_snapshot(current_gw_id)
            if daily_snapshot:
                teams_result.append({
                    **daily_snapshot,
                    "type": "daily_snapshot"
                })
        
        # Sort by gameweek descending (newest first)
        teams_result.sort(key=lambda x: x["gameweek"], reverse=True)
        
        return {"teams": teams_result}
    except Exception as e:
        logger.error(f"Error fetching selected teams: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{gameweek}")
async def get_selected_team(gameweek: int):
    """
    Get saved team for a specific gameweek.
    Returns daily snapshot for current/next gameweek, final team for past gameweeks.
    """
    try:
        deps = get_dependencies()
        fpl_client = deps.fpl_client
        db_manager = deps.db_manager
        
        # Get current/next gameweek
        next_gw = fpl_client.get_next_gameweek()
        current_gw_id = next_gw.id if next_gw else None
        
        # Determine if this is current/next or past gameweek
        is_current = current_gw_id and gameweek >= current_gw_id
        
        if is_current:
            # For current gameweek, prefer daily snapshot
            team = db_manager.get_latest_daily_snapshot(gameweek)
            if team:
                return {**team, "type": "daily_snapshot"}
            # Fallback to final team if no daily snapshot
            team = db_manager.get_selected_team(gameweek)
            if team:
                return {**team, "type": "final"}
        else:
            # For past gameweeks, use final team
            team = db_manager.get_selected_team(gameweek)
            if team:
                return {**team, "type": "final"}
        
        raise HTTPException(status_code=404, detail=f"No selected team found for Gameweek {gameweek}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching selected team for GW{gameweek}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

