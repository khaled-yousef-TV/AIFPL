"""
FPL Import Service

Handles importing teams from the FPL API.
"""

import logging
import requests
from typing import Dict, Any, Optional, List

from .dependencies import get_dependencies

logger = logging.getLogger(__name__)


async def import_fpl_team(team_id: int, gameweek: Optional[int] = None) -> Dict[str, Any]:
    """
    Import a team from FPL by team ID.
    
    Args:
        team_id: FPL team ID
        gameweek: Specific gameweek to import (None = fetch latest available)
        
    Returns:
        Dict with squad, bank, team_name, gameweek
        
    Raises:
        ValueError: If team not found or data unavailable
    """
    deps = get_dependencies()
    fpl_client = deps.fpl_client
    db_manager = deps.db_manager
    
    # If no specific gameweek requested, fetch the latest available (prioritize next > current > past)
    # Try to fetch picks from FPL API - will prioritize latest gameweek
    picks_data, used_gameweek = _fetch_team_picks(fpl_client, team_id, gameweek)
    
    if not picks_data or not picks_data.get("picks"):
        raise ValueError(f"No team data found for team {team_id}")
    
    picks = picks_data["picks"]
    
    # Get player and team data
    players = fpl_client.get_players()
    teams = fpl_client.get_teams()
    players_by_id = {p.id: p for p in players}
    teams_by_id = {t.id: t.short_name for t in teams}
    
    # Convert picks to squad format
    position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    squad = []
    
    for pick in picks:
        player_id = pick.get("element")
        player = players_by_id.get(player_id)
        if not player:
            continue
        
        # Use selling_price from picks data (user's actual selling price)
        # If not available, fall back to current player price
        # selling_price is in tenths (e.g., 100 = £10.0m), so divide by 10
        selling_price = pick.get("selling_price")
        if selling_price is not None:
            price = selling_price / 10.0
        else:
            # Fallback to current price if selling_price not available
            price = player.price
        
        squad.append({
            "id": player_id,
            "name": player.web_name,
            "position": position_map.get(player.element_type, "MID"),
            "price": price,
            "team": teams_by_id.get(player.team, "UNK"),
        })
    
    if not squad:
        raise ValueError("No valid players found in team")
    
    # Get bank and team name
    bank, team_name = _fetch_entry_data(fpl_client, team_id)
    
    # Save to database
    try:
        db_manager.save_fpl_team(team_id, team_name)
    except Exception as e:
        logger.warning(f"Failed to save FPL team to database: {e}")
    
    return {
        "squad": squad,
        "bank": bank,
        "team_name": team_name,
        "gameweek": used_gameweek,
    }


def _fetch_team_picks(fpl_client, team_id: int, gameweek: Optional[int] = None) -> tuple:
    """
    Fetch team picks from FPL API with fallback logic.
    
    Prioritizes latest gameweek when gameweek is None (for refreshing):
    - Next gameweek (most recent/latest team)
    - Current gameweek
    - Past gameweeks (as last resort)
    
    If gameweek is specified, tries that first, then falls back.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Accept': 'application/json',
    }
    
    current_gw = fpl_client.get_current_gameweek()
    next_gw = fpl_client.get_next_gameweek()
    
    # Build prioritized list of gameweeks to try
    gameweeks_to_try = []
    
    if gameweek is None:
        # When refreshing, prioritize latest gameweek first
        # Next gameweek has the most recent team state
        if next_gw:
            gameweeks_to_try.append(next_gw.id)
        if current_gw and current_gw.id not in gameweeks_to_try:
            gameweeks_to_try.append(current_gw.id)
        # Add past gameweeks as fallback (most recent first)
        if current_gw:
            for past in [current_gw.id - 1, current_gw.id - 2, current_gw.id - 3]:
                if past > 0 and past not in gameweeks_to_try:
                    gameweeks_to_try.append(past)
    else:
        # Specific gameweek requested - try that first, then fallback
        gameweeks_to_try.append(gameweek)
        # Add next/current if different
        if next_gw and next_gw.id != gameweek:
            gameweeks_to_try.append(next_gw.id)
        if current_gw and current_gw.id != gameweek and current_gw.id not in gameweeks_to_try:
            gameweeks_to_try.append(current_gw.id)
        # Add past gameweeks as fallback
        if current_gw:
            for past in [current_gw.id - 1, current_gw.id - 2, current_gw.id - 3]:
                if past > 0 and past not in gameweeks_to_try:
                    gameweeks_to_try.append(past)
    
    # Try each gameweek in priority order
    for gw in gameweeks_to_try:
        try:
            url = f"{fpl_client.BASE_URL}/entry/{team_id}/event/{gw}/picks/"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("picks"):
                    logger.info(f"Fetched picks for team {team_id} from GW{gw}")
                    return data, gw
        except Exception as e:
            logger.debug(f"Failed to fetch GW{gw}: {e}")
    
    # If all failed, return None with the requested gameweek (or next/current if None)
    fallback_gw = gameweek if gameweek else (next_gw.id if next_gw else (current_gw.id if current_gw else None))
    return None, fallback_gw


def _fetch_entry_data(fpl_client, team_id: int) -> tuple:
    """Fetch team entry data for bank and name."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Accept': 'application/json',
    }
    
    try:
        url = f"{fpl_client.BASE_URL}/entry/{team_id}/"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            bank = (data.get("last_deadline_bank", 0) or 0) / 10.0
            team_name = data.get("name", f"FPL Team {team_id}")
            return bank, team_name
    except Exception:
        pass
    
    return 0.0, f"FPL Team {team_id}"

