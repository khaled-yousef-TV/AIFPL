"""
Betting Odds Client

Fetch betting odds from The Odds API and convert to probabilities for FPL predictions.
"""

import logging
import os
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import requests
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class BettingOddsClient:
    """Client for fetching betting odds from The Odds API."""
    
    BASE_URL = "https://api.the-odds-api.com/v4"
    SPORT = "soccer_epl"  # Premier League
    REGIONS = "uk"  # UK bookmakers
    MARKETS = "h2h,spreads,totals"  # Head-to-head, spreads, totals
    
    # Cache for odds data
    _odds_cache: Dict[str, Tuple[Dict, datetime]] = {}
    CACHE_TTL = timedelta(hours=6)  # Cache odds for 6 hours
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the betting odds client.
        
        Args:
            api_key: The Odds API key (defaults to env var THE_ODDS_API_KEY)
        """
        self.api_key = api_key or os.getenv("THE_ODDS_API_KEY")
        enabled_str = os.getenv("BETTING_ODDS_ENABLED", "false")
        self.enabled = enabled_str.lower() == "true"
        self.weight = float(os.getenv("BETTING_ODDS_WEIGHT", "0.25"))
        
        # Debug logging
        logger.info(f"BettingOddsClient init: enabled_str='{enabled_str}', enabled={self.enabled}, has_api_key={bool(self.api_key)}")
        
        if self.enabled and not self.api_key:
            logger.warning("BETTING_ODDS_ENABLED is true but THE_ODDS_API_KEY not set. Odds will be disabled.")
            self.enabled = False
        
        if not self.enabled:
            logger.info(f"Betting odds disabled: enabled_str='{enabled_str}', enabled={self.enabled}, has_key={bool(self.api_key)}")
    
    def _is_cache_valid(self, cache_entry: Optional[Tuple[Dict, datetime]]) -> bool:
        """Check if cached odds are still valid."""
        if not cache_entry:
            return False
        _, cached_time = cache_entry
        return datetime.now() - cached_time < self.CACHE_TTL
    
    def _get_from_cache(self, key: str) -> Optional[Dict]:
        """Get odds from cache if valid."""
        cache_entry = self._odds_cache.get(key)
        if self._is_cache_valid(cache_entry):
            return cache_entry[0]
        return None
    
    def _store_in_cache(self, key: str, data: Dict):
        """Store odds in cache."""
        self._odds_cache[key] = (data, datetime.now())
    
    def get_fixture_odds(self, home_team: str, away_team: str) -> Optional[Dict]:
        """
        Get odds for a fixture.
        
        Args:
            home_team: Home team name (FPL team name)
            away_team: Away team name (FPL team name)
            
        Returns:
            Dictionary with odds data or None if unavailable
        """
        if not self.enabled:
            return None
        
        cache_key = f"{home_team}_{away_team}"
        cached = self._get_from_cache(cache_key)
        if cached:
            return cached
        
        try:
            # Map FPL team names to betting API team names
            # The Odds API uses different team name formats
            home_betting = self._map_team_name(home_team)
            away_betting = self._map_team_name(away_team)
            
            url = f"{self.BASE_URL}/sports/{self.SPORT}/odds"
            params = {
                "apiKey": self.api_key,
                "regions": self.REGIONS,
                "markets": self.MARKETS,
                "oddsFormat": "decimal"
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Find matching fixture
            fixture_odds = self._find_fixture_odds(data, home_betting, away_betting)
            
            if fixture_odds:
                self._store_in_cache(cache_key, fixture_odds)
            
            return fixture_odds
            
        except Exception as e:
            logger.error(f"Error fetching odds for {home_team} vs {away_team}: {e}")
            return None
    
    def _map_team_name(self, fpl_team_name: str) -> str:
        """
        Map FPL team name to betting API team name format.
        
        The Odds API may use different naming conventions.
        This is a basic mapping - may need adjustment based on actual API responses.
        """
        # Basic mappings for common variations
        team_mapping = {
            "Tottenham": "Tottenham Hotspur",
            "Spurs": "Tottenham Hotspur",
            "Man United": "Manchester United",
            "Man Utd": "Manchester United",
            "Man City": "Manchester City",
            "Brighton": "Brighton & Hove Albion",
            "Wolves": "Wolverhampton Wanderers",
            "Leicester": "Leicester City",
            "West Ham": "West Ham United",
            "Crystal Palace": "Crystal Palace",
            "Newcastle": "Newcastle United",
            "Norwich": "Norwich City",
            "Sheffield Utd": "Sheffield United",
            "Sheffield United": "Sheffield United",
        }
        
        # Check if we have a mapping
        if fpl_team_name in team_mapping:
            return team_mapping[fpl_team_name]
        
        # Otherwise return as-is (API may accept it)
        return fpl_team_name
    
    def _find_fixture_odds(self, odds_data: List[Dict], home_team: str, away_team: str) -> Optional[Dict]:
        """Find odds for a specific fixture in the API response."""
        if not odds_data:
            return None
        
        # The Odds API structure: each item has home_team and away_team fields
        for fixture in odds_data:
            fixture_home = fixture.get("home_team", "").lower()
            fixture_away = fixture.get("away_team", "").lower()
            
            # Check if this fixture matches
            if (home_team.lower() in fixture_home or fixture_home in home_team.lower()) and \
               (away_team.lower() in fixture_away or fixture_away in away_team.lower()):
                return self._parse_odds_response(fixture)
        
        return None
    
    def _parse_odds_response(self, fixture_data: Dict) -> Dict:
        """
        Parse odds response and extract relevant markets.
        
        Returns:
            Dictionary with:
            - home_win_prob: Probability of home team winning
            - away_win_prob: Probability of away team winning
            - draw_prob: Probability of draw
            - btts_prob: Probability of both teams to score
            - over_2_5_prob: Probability of over 2.5 goals
        """
        parsed = {
            "home_win_prob": 0.5,
            "away_win_prob": 0.5,
            "draw_prob": 0.2,
            "btts_prob": 0.5,
            "over_2_5_prob": 0.5,
        }
        
        # Parse bookmaker odds
        bookmakers = fixture_data.get("bookmakers", [])
        if not bookmakers:
            return parsed
        
        # Aggregate odds across bookmakers (use first available for now)
        bookmaker = bookmakers[0] if bookmakers else {}
        markets = bookmaker.get("markets", [])
        
        for market in markets:
            market_key = market.get("key")
            
            if market_key == "h2h":  # Head-to-head (match winner)
                outcomes = market.get("outcomes", [])
                for outcome in outcomes:
                    name = outcome.get("name", "").lower()
                    odds = outcome.get("price", 0)
                    if odds > 0:
                        prob = 1.0 / odds
                        if "home" in name or name == fixture_data.get("home_team", "").lower():
                            parsed["home_win_prob"] = prob
                        elif "away" in name or name == fixture_data.get("away_team", "").lower():
                            parsed["away_win_prob"] = prob
                        elif "draw" in name:
                            parsed["draw_prob"] = prob
            
            elif market_key == "totals":  # Over/Under totals
                outcomes = market.get("outcomes", [])
                for outcome in outcomes:
                    name = outcome.get("name", "").lower()
                    odds = outcome.get("price", 0)
                    if odds > 0 and "over" in name and "2.5" in name:
                        parsed["over_2_5_prob"] = 1.0 / odds
        
        # Estimate BTTS from totals (if not available directly)
        # High over_2_5 probability suggests BTTS likely
        if parsed["over_2_5_prob"] > 0.5:
            parsed["btts_prob"] = min(0.8, parsed["over_2_5_prob"] * 1.2)
        
        return parsed
    
    def get_player_goalscorer_odds(self, player_name: str, fixture_odds: Dict) -> float:
        """
        Get anytime goalscorer odds for a player.
        
        Note: The Odds API doesn't directly provide player-specific goalscorer odds
        in the free tier. This is a placeholder that estimates based on team odds.
        
        For MVP, we'll use team win probability as a proxy for attacking potential.
        
        Args:
            player_name: Player name
            fixture_odds: Fixture odds dictionary from get_fixture_odds()
            
        Returns:
            Estimated probability (0-1) of player scoring
        """
        if not fixture_odds:
            return 0.0
        
        # For MVP, use team win probability as a rough proxy
        # Premium attackers on favored teams are more likely to score
        # This is a simplified approach - in Phase 2 we can integrate a separate
        # API that provides player-specific goalscorer odds
        team_win_prob = fixture_odds.get("home_win_prob", 0.5)
        
        # Rough estimate: top attackers on favored teams have higher scoring probability
        # We'll refine this in Phase 2 with actual player-specific odds
        estimated_prob = team_win_prob * 0.4  # Base multiplier
        
        # Adjust based on player name (premium players)
        premium_players = ["haaland", "salah", "kane", "son", "de bruyne", "saka", "martinelli"]
        if any(prem in player_name.lower() for prem in premium_players):
            estimated_prob *= 1.5
        
        return min(0.6, estimated_prob)  # Cap at 60%
    
    def get_clean_sheet_probability(self, is_home: bool, fixture_odds: Dict) -> float:
        """
        Estimate clean sheet probability for a team.
        
        Args:
            is_home: Whether the team is playing at home
            fixture_odds: Fixture odds dictionary
            
        Returns:
            Clean sheet probability (0-1)
        """
        if not fixture_odds:
            return 0.3  # Default estimate
        
        # BTTS probability is inverse of clean sheet probability (roughly)
        btts_prob = fixture_odds.get("btts_prob", 0.5)
        cs_prob = (1.0 - btts_prob) * 0.8  # Rough conversion
        
        # Adjust for home advantage
        if is_home:
            cs_prob *= 1.1
        
        # Use team win probability as additional signal
        team_win_prob = fixture_odds.get("home_win_prob" if is_home else "away_win_prob", 0.5)
        cs_prob = (cs_prob + (team_win_prob * 0.3)) / 1.3
        
        return max(0.1, min(0.7, cs_prob))  # Bound between 10% and 70%
    
    def normalize_player_name(self, name: str) -> str:
        """Normalize player name for matching."""
        # Convert to lowercase, remove accents, remove periods
        name = name.lower()
        name = name.replace(".", "").replace("'", "").replace("-", " ")
        # Remove common prefixes/suffixes
        name = name.replace(" jr", "").replace(" sr", "")
        return name.strip()
    
    def match_player_name(self, fpl_name: str, betting_names: List[str]) -> Optional[str]:
        """
        Match FPL player name to betting market name using fuzzy matching.
        
        Args:
            fpl_name: Player name from FPL API
            betting_names: List of player names from betting markets
            
        Returns:
            Matched name or None
        """
        if not betting_names:
            return None
        
        fpl_normalized = self.normalize_player_name(fpl_name)
        
        best_match = None
        best_score = 0.0
        
        for betting_name in betting_names:
            betting_normalized = self.normalize_player_name(betting_name)
            
            # Exact match after normalization
            if fpl_normalized == betting_normalized:
                return betting_name
            
            # Fuzzy match score
            score = SequenceMatcher(None, fpl_normalized, betting_normalized).ratio()
            
            # Check if last names match (common pattern)
            fpl_parts = fpl_normalized.split()
            betting_parts = betting_normalized.split()
            
            if len(fpl_parts) > 0 and len(betting_parts) > 0:
                if fpl_parts[-1] == betting_parts[-1]:  # Last name matches
                    score = max(score, 0.8)
            
            if score > best_score and score > 0.7:  # Threshold for match
                best_score = score
                best_match = betting_name
        
        return best_match

