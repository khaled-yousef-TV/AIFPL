"""
FPL API Client

Wrapper for the Fantasy Premier League API.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import time
import requests

from .auth import FPLAuth
from .models import (
    Player, Team, Fixture, GameWeek, MyTeam, MyTeamPlayer,
    Transfer, TransferPayload, BootstrapData
)

logger = logging.getLogger(__name__)


class FPLClient:
    """Client for interacting with the FPL API."""
    
    BASE_URL = "https://fantasy.premierleague.com/api"
    
    # Rate limiting
    # Keep this conservative, but fast enough for local iteration.
    # Public FPL endpoints are fairly tolerant, and we also cache aggressively below.
    MIN_REQUEST_INTERVAL = 0.25  # seconds between requests
    
    def __init__(self, auth: Optional[FPLAuth] = None):
        """
        Initialize the FPL client.
        
        Args:
            auth: FPLAuth instance for authenticated requests (optional for public data)
        """
        self.auth = auth  # Can be None for public-only access
        self._session = requests.Session()
        self._last_request_time = 0
        
        # Cache
        self._bootstrap_cache: Optional[Dict[str, Any]] = None
        self._bootstrap_cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=30)
        
        # Derived model caches (built from bootstrap-static)
        self._models_cache_time: Optional[datetime] = None
        self._players_models_cache: Optional[List[Player]] = None
        self._players_by_id: Dict[int, Player] = {}
        self._teams_models_cache: Optional[List[Team]] = None
        self._teams_by_id: Dict[int, Team] = {}
        self._gameweeks_models_cache: Optional[List[GameWeek]] = None
        
        # Fixtures cache (keyed by gameweek id or "all")
        self._fixtures_cache: Dict[str, Dict[str, Any]] = {}
    
    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.MIN_REQUEST_INTERVAL:
            time.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()
    
    def _get(self, endpoint: str, authenticated: bool = False) -> Dict[str, Any]:
        """
        Make a GET request to the FPL API.
        
        Args:
            endpoint: API endpoint (without base URL)
            authenticated: Whether to use authenticated session
            
        Returns:
            JSON response as dictionary
        """
        self._rate_limit()
        
        url = f"{self.BASE_URL}/{endpoint}"
        
        if authenticated and self.auth:
            session = self.auth.get_session()
        else:
            session = self._session
        
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 503:
                logger.warning(f"FPL API temporarily unavailable (503): {url}. This may be rate limiting or maintenance.")
                # Return empty result instead of crashing for 503s
                if "fixtures" in endpoint:
                    return []
                return {}
            logger.error(f"API request failed: {e}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise
    
    def _post(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make a POST request to the FPL API (authenticated).
        
        Args:
            endpoint: API endpoint
            data: JSON payload
            
        Returns:
            JSON response
        """
        self._rate_limit()
        
        url = f"{self.BASE_URL}/{endpoint}"
        session = self.auth.get_session()
        
        try:
            response = session.post(url, json=data, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API POST failed: {e}")
            raise
    
    # ==================== Public Data ====================
    
    def get_bootstrap(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Get bootstrap-static data (players, teams, gameweeks).
        
        This is the main data endpoint containing all player and team info.
        Results are cached for 30 minutes.
        
        Args:
            force_refresh: Force refresh cache
            
        Returns:
            Bootstrap data dictionary
        """
        now = datetime.now()
        
        if (not force_refresh and 
            self._bootstrap_cache is not None and
            self._bootstrap_cache_time is not None and
            now - self._bootstrap_cache_time < self._cache_ttl):
            return self._bootstrap_cache
        
        data = self._get("bootstrap-static/")
        self._bootstrap_cache = data
        self._bootstrap_cache_time = now
        
        # Invalidate derived caches whenever bootstrap refreshes.
        self._models_cache_time = None
        self._players_models_cache = None
        self._players_by_id = {}
        self._teams_models_cache = None
        self._teams_by_id = {}
        self._gameweeks_models_cache = None
        
        return data

    def _ensure_models_cache(self) -> None:
        """Build Player/Team/GameWeek model caches from bootstrap data if needed."""
        data = self.get_bootstrap()
        if self._bootstrap_cache_time is None:
            return

        if (
            self._models_cache_time == self._bootstrap_cache_time
            and self._players_models_cache is not None
        ):
            return

        players = [Player(**p) for p in data.get("elements", [])]
        teams = [Team(**t) for t in data.get("teams", [])]
        gameweeks = [GameWeek(**gw) for gw in data.get("events", [])]

        self._players_models_cache = players
        self._players_by_id = {p.id: p for p in players}
        self._teams_models_cache = teams
        self._teams_by_id = {t.id: t for t in teams}
        self._gameweeks_models_cache = gameweeks
        self._models_cache_time = self._bootstrap_cache_time
    
    def get_players(self) -> List[Player]:
        """Get all players."""
        self._ensure_models_cache()
        return self._players_models_cache or []
    
    def get_player(self, player_id: int) -> Optional[Player]:
        """Get a specific player by ID."""
        self._ensure_models_cache()
        return self._players_by_id.get(player_id)
    
    def get_player_details(self, player_id: int) -> Dict[str, Any]:
        """
        Get detailed player info including fixture history.
        
        Args:
            player_id: Player ID
            
        Returns:
            Player details with history
        """
        return self._get(f"element-summary/{player_id}/")
    
    def get_teams(self) -> List[Team]:
        """Get all teams."""
        self._ensure_models_cache()
        return self._teams_models_cache or []
    
    def get_team(self, team_id: int) -> Optional[Team]:
        """Get a specific team by ID."""
        self._ensure_models_cache()
        return self._teams_by_id.get(team_id)
    
    def get_gameweeks(self) -> List[GameWeek]:
        """Get all gameweeks."""
        self._ensure_models_cache()
        return self._gameweeks_models_cache or []
    
    def get_current_gameweek(self) -> Optional[GameWeek]:
        """Get the current gameweek."""
        gameweeks = self.get_gameweeks()
        for gw in gameweeks:
            if gw.is_current:
                return gw
        # If no current, get next
        for gw in gameweeks:
            if gw.is_next:
                return gw
        return None
    
    def get_next_gameweek(self) -> Optional[GameWeek]:
        """Get the next gameweek."""
        gameweeks = self.get_gameweeks()
        for gw in gameweeks:
            if gw.is_next:
                return gw
        return None
    
    def get_fixtures(self, gameweek: Optional[int] = None) -> List[Fixture]:
        """
        Get fixtures.
        
        Args:
            gameweek: Filter by gameweek (optional)
            
        Returns:
            List of fixtures
        """
        key = str(gameweek) if gameweek else "all"
        now = datetime.now()
        cached = self._fixtures_cache.get(key)
        if cached and (now - cached["time"] < self._cache_ttl):
            return cached["data"]

        endpoint = "fixtures/"
        if gameweek:
            endpoint += f"?event={gameweek}"

        data = self._get(endpoint)
        fixtures = [Fixture(**f) for f in data]
        self._fixtures_cache[key] = {"time": now, "data": fixtures}
        return fixtures
    
    # ==================== Authenticated Endpoints ====================
    
    def login(self) -> bool:
        """Login to FPL."""
        return self.auth.login()
    
    def get_my_team(self) -> MyTeam:
        """
        Get the authenticated user's current team.
        
        Returns:
            MyTeam with current picks
        """
        if not self.auth.team_id:
            raise ValueError("Not authenticated. Call login() first.")
        
        data = self._get(f"my-team/{self.auth.team_id}/", authenticated=True)
        
        picks = [MyTeamPlayer(**p) for p in data.get("picks", [])]
        
        return MyTeam(
            picks=picks,
            chips=data.get("chips", []),
            transfers=data.get("transfers", {})
        )
    
    def get_my_team_info(self) -> Dict[str, Any]:
        """Get detailed info about user's team."""
        if not self.auth.team_id:
            raise ValueError("Not authenticated. Call login() first.")
        
        return self._get(f"entry/{self.auth.team_id}/", authenticated=True)
    
    def get_my_transfers(self) -> List[Dict[str, Any]]:
        """Get user's transfer history."""
        if not self.auth.team_id:
            raise ValueError("Not authenticated. Call login() first.")
        
        data = self._get(f"entry/{self.auth.team_id}/transfers/", authenticated=True)
        return data
    
    def get_my_history(self) -> Dict[str, Any]:
        """Get user's season history."""
        if not self.auth.team_id:
            raise ValueError("Not authenticated. Call login() first.")
        
        return self._get(f"entry/{self.auth.team_id}/history/", authenticated=True)
    
    def make_transfers(self, transfers: List[Transfer], wildcard: bool = False, freehit: bool = False) -> Dict[str, Any]:
        """
        Make transfers.
        
        Args:
            transfers: List of transfers to make
            wildcard: Use wildcard chip
            freehit: Use free hit chip
            
        Returns:
            API response
        """
        if not self.auth.team_id:
            raise ValueError("Not authenticated. Call login() first.")
        
        next_gw = self.get_next_gameweek()
        if not next_gw:
            raise ValueError("No upcoming gameweek found")
        
        chip = None
        if wildcard:
            chip = "wildcard"
        elif freehit:
            chip = "freehit"
        
        payload = {
            "chip": chip,
            "entry": self.auth.team_id,
            "event": next_gw.id,
            "transfers": [
                {
                    "element_in": t.element_in,
                    "element_out": t.element_out,
                    "purchase_price": t.purchase_price,
                    "selling_price": t.selling_price,
                }
                for t in transfers
            ]
        }
        
        return self._post(f"my-team/{self.auth.team_id}/", payload)
    
    def set_lineup(
        self,
        starting_ids: List[int],
        bench_ids: List[int],
        captain_id: int,
        vice_captain_id: int
    ) -> Dict[str, Any]:
        """
        Set team lineup.
        
        Args:
            starting_ids: List of 11 player IDs for starting XI
            bench_ids: List of 4 player IDs for bench (in order)
            captain_id: Captain player ID
            vice_captain_id: Vice captain player ID
            
        Returns:
            API response
        """
        if not self.auth.team_id:
            raise ValueError("Not authenticated. Call login() first.")
        
        if len(starting_ids) != 11:
            raise ValueError("Must have exactly 11 starting players")
        if len(bench_ids) != 4:
            raise ValueError("Must have exactly 4 bench players")
        
        picks = []
        
        # Starting XI (positions 1-11)
        for i, player_id in enumerate(starting_ids, 1):
            picks.append({
                "element": player_id,
                "position": i,
                "is_captain": player_id == captain_id,
                "is_vice_captain": player_id == vice_captain_id,
            })
        
        # Bench (positions 12-15)
        for i, player_id in enumerate(bench_ids, 12):
            picks.append({
                "element": player_id,
                "position": i,
                "is_captain": False,
                "is_vice_captain": False,
            })
        
        payload = {"picks": picks}
        
        return self._post(f"my-team/{self.auth.team_id}/", payload)
    
    # ==================== Helper Methods ====================
    
    def get_player_by_name(self, name: str) -> Optional[Player]:
        """Find a player by name (partial match)."""
        name_lower = name.lower()
        players = self.get_players()
        
        for player in players:
            if (name_lower in player.web_name.lower() or
                name_lower in player.full_name.lower()):
                return player
        
        return None
    
    def get_players_by_team(self, team_id: int) -> List[Player]:
        """Get all players from a specific team."""
        players = self.get_players()
        return [p for p in players if p.team == team_id]
    
    def get_players_by_position(self, position: int) -> List[Player]:
        """
        Get all players by position.
        
        Args:
            position: 1=GK, 2=DEF, 3=MID, 4=FWD
        """
        players = self.get_players()
        return [p for p in players if p.element_type == position]
    
    def get_top_players(self, n: int = 20, position: Optional[int] = None) -> List[Player]:
        """
        Get top N players by total points.
        
        Args:
            n: Number of players to return
            position: Optional position filter
        """
        players = self.get_players()
        
        if position:
            players = [p for p in players if p.element_type == position]
        
        players.sort(key=lambda p: p.total_points, reverse=True)
        return players[:n]
    
    def get_deadline(self) -> Optional[datetime]:
        """Get the deadline for the next gameweek."""
        gw = self.get_next_gameweek()
        return gw.deadline_time if gw else None

