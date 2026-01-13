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
    
    # If no specific gameweek requested, get the latest team state
    if gameweek is None:
        next_gw = fpl_client.get_next_gameweek()
        current_gw = fpl_client.get_current_gameweek()
        
        # Step 1: Get next gameweek picks (most recent team state)
        # Keep trying until GW22 picks are available - they contain the latest transfers
        if next_gw:
            picks_data, used_gameweek = _fetch_team_picks_with_retry(fpl_client, team_id, next_gw.id)
            if picks_data and picks_data.get("picks"):
                picks = picks_data["picks"]
            else:
                # Fallback to current gameweek if next GW truly unavailable
                gameweek = current_gw.id if current_gw else None
                picks_data, used_gameweek = _fetch_team_picks(fpl_client, team_id, gameweek)
                if not picks_data or not picks_data.get("picks"):
                    raise ValueError(f"No team data found for team {team_id}")
                picks = picks_data["picks"]
        else:
            # No next gameweek - use current
            gameweek = current_gw.id if current_gw else None
            picks_data, used_gameweek = _fetch_team_picks(fpl_client, team_id, gameweek)
            if not picks_data or not picks_data.get("picks"):
                raise ValueError(f"No team data found for team {team_id}")
            picks = picks_data["picks"]
    else:
        # Specific gameweek requested - fetch it
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


def _fetch_team_picks_with_retry(fpl_client, team_id: int, gameweek: int, max_retries: int = 5) -> tuple:
    """
    Fetch team picks with aggressive retries until available.
    Used for next gameweek picks that may not be available immediately.
    """
    import time
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Accept': 'application/json',
    }
    
    for attempt in range(max_retries):
        try:
            url = f"{fpl_client.BASE_URL}/entry/{team_id}/event/{gameweek}/picks/"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("picks"):
                    return data, gameweek
        except Exception:
            pass
        
        # Wait before retry (exponential backoff: 0.5s, 1s, 2s, 4s)
        if attempt < max_retries - 1:
            time.sleep(0.5 * (2 ** attempt))
    
    return None, gameweek


def _fetch_team_picks(fpl_client, team_id: int, gameweek: Optional[int] = None) -> tuple:
    """
    Fetch team picks from FPL API with fallback logic.
    
    When gameweek is None (refreshing), tries all available gameweeks and returns
    the one with the highest gameweek number (most recent team state).
    
    If gameweek is specified, tries that first, then falls back to find latest.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Accept': 'application/json',
    }
    
    current_gw = fpl_client.get_current_gameweek()
    next_gw = fpl_client.get_next_gameweek()
    
    # Check if there are transfers for next gameweek (indicating team was updated)
    has_next_gw_transfers = False
    if next_gw:
        try:
            transfers_url = f"{fpl_client.BASE_URL}/entry/{team_id}/transfers/"
            transfers_response = requests.get(transfers_url, headers=headers, timeout=10)
            if transfers_response.status_code == 200:
                transfers = transfers_response.json()
                if transfers and isinstance(transfers, list):
                    # Check if any transfer is for next gameweek
                    for transfer in transfers[:10]:  # Check last 10 transfers
                        if transfer.get("event") == next_gw.id:
                            has_next_gw_transfers = True
                            logger.info(f"Found transfers for GW{next_gw.id} - team may have been updated")
                            break
        except Exception as e:
            logger.debug(f"Could not check transfers for next GW: {e}")
    
    # Build list of gameweeks to try
    gameweeks_to_try = []
    
    if gameweek is None:
        # When refreshing, prioritize next gameweek if it has transfers
        # This ensures we get the latest team state after transfers
        if next_gw:
            gameweeks_to_try.append(next_gw.id)
        if current_gw and current_gw.id not in gameweeks_to_try:
            gameweeks_to_try.append(current_gw.id)
        # Add recent past gameweeks (but only if next GW doesn't have transfers)
        if current_gw and not has_next_gw_transfers:
            for past in [current_gw.id - 1, current_gw.id - 2, current_gw.id - 3, current_gw.id - 4, current_gw.id - 5]:
                if past > 0 and past not in gameweeks_to_try:
                    gameweeks_to_try.append(past)
    else:
        # Specific gameweek requested - try that first
        gameweeks_to_try.append(gameweek)
        # Also try next/current and recent past to find latest
        if next_gw and next_gw.id != gameweek:
            gameweeks_to_try.append(next_gw.id)
        if current_gw and current_gw.id != gameweek and current_gw.id not in gameweeks_to_try:
            gameweeks_to_try.append(current_gw.id)
        if current_gw and not has_next_gw_transfers:
            for past in [current_gw.id - 1, current_gw.id - 2, current_gw.id - 3]:
                if past > 0 and past not in gameweeks_to_try:
                    gameweeks_to_try.append(past)
    
    # Try all gameweeks and collect successful responses
    successful_fetches = []  # List of (data, gameweek) tuples
    
    for gw in gameweeks_to_try:
        try:
            url = f"{fpl_client.BASE_URL}/entry/{team_id}/event/{gw}/picks/"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("picks"):
                    successful_fetches.append((data, gw))
                    logger.debug(f"Successfully fetched picks for team {team_id} from GW{gw}")
        except Exception as e:
            logger.debug(f"Failed to fetch GW{gw}: {e}")
    
    # Return the response with the highest gameweek number (most recent)
    if successful_fetches:
        # Sort by gameweek number descending (highest first)
        successful_fetches.sort(key=lambda x: x[1], reverse=True)
        latest_data, latest_gw = successful_fetches[0]
        
        # If we found next GW transfers but couldn't get next GW picks, log a warning
        if has_next_gw_transfers and latest_gw < (next_gw.id if next_gw else 0):
            logger.warning(f"Team {team_id} has transfers for GW{next_gw.id if next_gw else '?'} but picks not available yet. Using GW{latest_gw} (may be outdated).")
        elif next_gw and latest_gw < next_gw.id:
            # Even without explicit transfers, if we're using an old gameweek when next exists, warn
            logger.info(f"Team {team_id}: Next gameweek {next_gw.id} picks not available yet, using GW{latest_gw}. Team may have pending transfers.")
        
        logger.info(f"Using picks for team {team_id} from GW{latest_gw} (tried {len(successful_fetches)} gameweeks)")
        return latest_data, latest_gw
    
    # If all failed, return None with the requested gameweek (or next/current if None)
    fallback_gw = gameweek if gameweek else (next_gw.id if next_gw else (current_gw.id if current_gw else None))
    return None, fallback_gw


def _try_reconstruct_team_from_transfers(fpl_client, team_id: int) -> Optional[Dict[str, Any]]:
    """
    Try to reconstruct the current team by applying transfers to the last known picks.
    
    This is useful when the next gameweek picks aren't available yet but transfers have been made.
    
    Note: The FPL public API only shows confirmed transfers, not pending ones. If a transfer
    was made for the next gameweek but hasn't been confirmed yet, it won't appear in the API.
    In that case, we must wait for the next gameweek picks to become available.
    
    Returns:
        Dict with squad, bank, team_name, gameweek if successful, None otherwise
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Accept': 'application/json',
    }
    
    current_gw = fpl_client.get_current_gameweek()
    next_gw = fpl_client.get_next_gameweek()
    
    if not current_gw or not next_gw:
        return None
    
    # IMPORTANT: First, double-check if next GW picks are available now
    # (They might have become available since the initial check)
    next_gw_picks_url = f"{fpl_client.BASE_URL}/entry/{team_id}/event/{next_gw.id}/picks/"
    try:
        next_response = requests.get(next_gw_picks_url, headers=headers, timeout=10)
        if next_response.status_code == 200:
            next_data = next_response.json()
            if next_data.get("picks"):
                logger.info(f"Next gameweek {next_gw.id} picks are now available!")
                # Return None to let the main function handle it with proper processing
                return None
    except Exception:
        pass
    
    # Get the last available gameweek picks (usually current GW)
    last_picks_data, last_gw = _fetch_team_picks(fpl_client, team_id, current_gw.id)
    if not last_picks_data or not last_picks_data.get("picks"):
        return None
    
    # Get transfer history
    try:
        transfers_url = f"{fpl_client.BASE_URL}/entry/{team_id}/transfers/"
        transfers_response = requests.get(transfers_url, headers=headers, timeout=10)
        
        if transfers_response.status_code != 200:
            return None
        
        transfers = transfers_response.json()
        if not transfers or not isinstance(transfers, list):
            return None
        
        # Filter transfers made for next gameweek (or after last known gameweek)
        relevant_transfers = [
            t for t in transfers
            if t.get("event") and t.get("event") > last_gw
        ]
        
        if not relevant_transfers:
            # No transfers for future gameweeks in API
            # This could mean:
            # 1. No transfers were made, OR
            # 2. Transfers are pending and not yet in the API
            logger.info(f"No transfers found for GW{next_gw.id} in API. Transfers may be pending and not yet confirmed.")
            return None
        
        logger.info(f"Found {len(relevant_transfers)} transfers after GW{last_gw}, reconstructing team...")
        
        # Get player data
        players = fpl_client.get_players()
        teams = fpl_client.get_teams()
        players_by_id = {p.id: p for p in players}
        teams_by_id = {t.id: t.short_name for t in teams}
        
        # Start with last known picks
        current_picks = {pick.get("element"): pick for pick in last_picks_data["picks"]}
        
        # Apply transfers (most recent first, so we apply them in reverse)
        for transfer in reversed(relevant_transfers):
            element_out = transfer.get("element_out")
            element_in = transfer.get("element_in")
            
            if element_out and element_out in current_picks:
                # Remove the player being transferred out
                del current_picks[element_out]
            
            if element_in:
                # Add the player being transferred in
                # Use the selling price from the transfer if available
                selling_price = transfer.get("element_in_cost", 0)
                if selling_price == 0:
                    # Fallback to current player price
                    player = players_by_id.get(element_in)
                    if player:
                        selling_price = player.now_cost
                
                current_picks[element_in] = {
                    "element": element_in,
                    "selling_price": selling_price,
                    "purchase_price": selling_price,
                    "position": len(current_picks) + 1,  # Will be corrected below
                }
        
        # Convert to squad format
        position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
        squad = []
        
        for pick in current_picks.values():
            player_id = pick.get("element")
            player = players_by_id.get(player_id)
            if not player:
                continue
            
            selling_price = pick.get("selling_price", 0)
            price = selling_price / 10.0 if selling_price > 0 else player.price
            
            squad.append({
                "id": player_id,
                "name": player.web_name,
                "position": position_map.get(player.element_type, "MID"),
                "price": price,
                "team": teams_by_id.get(player.team, "UNK"),
            })
        
        if len(squad) != 15:
            logger.warning(f"Reconstructed squad has {len(squad)} players, expected 15. Using normal flow instead.")
            return None
        
        # Get bank and team name
        bank, team_name = _fetch_entry_data(fpl_client, team_id)
        
        # Adjust bank based on transfers
        for transfer in relevant_transfers:
            element_out_cost = transfer.get("element_out_cost", 0) / 10.0
            element_in_cost = transfer.get("element_in_cost", 0) / 10.0
            bank += element_out_cost - element_in_cost
        
        logger.info(f"Successfully reconstructed team {team_id} with {len(squad)} players")
        
        return {
            "squad": squad,
            "bank": bank,
            "team_name": team_name,
            "gameweek": next_gw.id,
        }
        
    except Exception as e:
        logger.debug(f"Could not reconstruct team from transfers: {e}")
        return None


def _determine_latest_gameweek(fpl_client, team_id: int) -> Optional[int]:
    """
    Determine the latest gameweek that has team data after transfers.
    
    Strategy:
    1. Check transfer history to find the latest gameweek with transfers
    2. If no transfers found, use next/current gameweek
    3. This ensures we get the most recent team state
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Accept': 'application/json',
    }
    
    # First, try to get transfer history to find the latest gameweek with changes
    try:
        url = f"{fpl_client.BASE_URL}/entry/{team_id}/transfers/"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            transfers = response.json()
            if transfers and isinstance(transfers, list) and len(transfers) > 0:
                # Get the most recent transfer's gameweek
                # Transfers are typically ordered by most recent first
                latest_transfer = transfers[0]
                transfer_gw = latest_transfer.get("event")
                if transfer_gw:
                    logger.info(f"Found latest transfer in GW{transfer_gw} for team {team_id}")
                    # Use the gameweek after the transfer (or current if transfer was this GW)
                    current_gw = fpl_client.get_current_gameweek()
                    next_gw = fpl_client.get_next_gameweek()
                    
                    # If transfer was in current GW, use next GW (if available) or current
                    if current_gw and transfer_gw == current_gw.id:
                        return next_gw.id if next_gw else current_gw.id
                    # If transfer was in a past GW, use next GW (most recent team state)
                    elif next_gw:
                        return next_gw.id
                    elif current_gw:
                        return current_gw.id
                    else:
                        return transfer_gw
    except Exception as e:
        logger.debug(f"Could not fetch transfer history for team {team_id}: {e}")
    
    # Fallback: use next/current gameweek
    next_gw = fpl_client.get_next_gameweek()
    current_gw = fpl_client.get_current_gameweek()
    
    if next_gw:
        logger.info(f"Using next gameweek {next_gw.id} for team {team_id}")
        return next_gw.id
    elif current_gw:
        logger.info(f"Using current gameweek {current_gw.id} for team {team_id}")
        return current_gw.id
    
    return None


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

