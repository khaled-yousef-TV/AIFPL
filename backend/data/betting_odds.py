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
    
    def _fetch_all_odds(self) -> Optional[List[Dict]]:
        """
        Fetch all available odds from The Odds API (cached).
        This is more efficient than fetching per-fixture.
        """
        cache_key = "_all_odds"
        cached = self._get_from_cache(cache_key)
        if cached:
            return cached
        
        try:
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
            
            # Cache the full response
            self._store_in_cache(cache_key, data)
            return data
            
        except Exception as e:
            logger.error(f"Error fetching odds from The Odds API: {e}")
            return None
    
    def get_fixture_odds(self, home_team: str, away_team: str, all_odds_data: Optional[List[Dict]] = None) -> Optional[Dict]:
        """
        Get odds for a fixture.
        
        Args:
            home_team: Home team name (FPL team name)
            away_team: Away team name (FPL team name)
            all_odds_data: Pre-fetched odds data (optional, to avoid redundant API calls)
            
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
            # Map FPL team names to betting API team name variations
            home_variations = self._map_team_name(home_team)
            away_variations = self._map_team_name(away_team)
            
            # Use provided data or fetch if not provided
            if all_odds_data is None:
                all_odds_data = self._fetch_all_odds()
            
            if not all_odds_data:
                return None
            
            # Find matching fixture
            fixture_odds = self._find_fixture_odds(all_odds_data, home_variations, away_variations)
            
            if fixture_odds:
                self._store_in_cache(cache_key, fixture_odds)
            else:
                # Log available teams from betting API for debugging
                available_teams = set()
                for fixture in all_odds_data[:5]:  # Check first 5 fixtures
                    available_teams.add(fixture.get("home_team", ""))
                    available_teams.add(fixture.get("away_team", ""))
                logger.debug(
                    f"No odds match for {home_team} vs {away_team}. "
                    f"Tried variations: {home_variations[:2]} vs {away_variations[:2]}. "
                    f"Sample teams in API: {list(available_teams)[:4]}"
                )
            
            return fixture_odds
            
        except Exception as e:
            logger.error(f"Error processing odds for {home_team} vs {away_team}: {e}")
            return None
    
    def _map_team_name(self, fpl_team_name: str) -> List[str]:
        """
        Map FPL team name to possible betting API team name variations.
        
        Returns a list of possible names to try (most likely first).
        The Odds API may use different naming conventions, so we try multiple variations.
        """
        # Comprehensive mapping: FPL name -> [possible betting API names, ordered by likelihood]
        team_mapping = {
            "Arsenal": ["Arsenal", "Arsenal FC"],
            "Aston Villa": ["Aston Villa", "Aston Villa FC"],
            "Bournemouth": ["Bournemouth", "AFC Bournemouth"],
            "Brentford": ["Brentford", "Brentford FC"],
            "Brighton": ["Brighton & Hove Albion", "Brighton", "Brighton Hove Albion"],
            "Chelsea": ["Chelsea", "Chelsea FC"],
            "Crystal Palace": ["Crystal Palace", "Crystal Palace FC", "Palace"],
            "Everton": ["Everton", "Everton FC"],
            "Fulham": ["Fulham", "Fulham FC"],
            "Ipswich": ["Ipswich Town", "Ipswich"],
            "Leicester": ["Leicester City", "Leicester"],
            "Liverpool": ["Liverpool", "Liverpool FC"],
            "Man City": ["Manchester City", "Man City", "Man. City"],
            "Man United": ["Manchester United", "Man United", "Man Utd", "Man. United"],
            "Man Utd": ["Manchester United", "Man United", "Man Utd", "Man. United"],
            "Newcastle": ["Newcastle United", "Newcastle", "Newcastle Utd"],
            "Nott'm Forest": ["Nottingham Forest", "Nott'm Forest", "Nottingham"],
            "Nottingham Forest": ["Nottingham Forest", "Nott'm Forest", "Nottingham"],
            "Sheffield Utd": ["Sheffield United", "Sheffield Utd", "Sheffield"],
            "Sheffield United": ["Sheffield United", "Sheffield Utd", "Sheffield"],
            "Spurs": ["Tottenham Hotspur", "Tottenham", "Spurs"],
            "Tottenham": ["Tottenham Hotspur", "Tottenham", "Spurs"],
            "West Ham": ["West Ham United", "West Ham", "West Ham Utd"],
            "Wolves": ["Wolverhampton Wanderers", "Wolves", "Wolverhampton"],
        }
        
        # Normalize the input (trim, handle variations)
        normalized = fpl_team_name.strip()
        
        # Check exact match first
        if normalized in team_mapping:
            return team_mapping[normalized]
        
        # Try case-insensitive match
        for key, values in team_mapping.items():
            if key.lower() == normalized.lower():
                return values
        
        # If no mapping found, return original and some variations
        return [normalized, normalized.replace(" ", "")]
    
    def _find_fixture_odds(self, odds_data: List[Dict], home_team_variations: List[str], away_team_variations: List[str]) -> Optional[Dict]:
        """
        Find odds for a specific fixture in the API response.
        
        Args:
            odds_data: List of fixtures from The Odds API
            home_team_variations: List of possible home team names to try
            away_team_variations: List of possible away team names to try
        """
        if not odds_data:
            return None
        
        # The Odds API structure: each item has home_team and away_team fields
        for fixture in odds_data:
            fixture_home = fixture.get("home_team", "").strip()
            fixture_away = fixture.get("away_team", "").strip()
            
            # Try all combinations of team name variations
            for home_var in home_team_variations:
                for away_var in away_team_variations:
                    if self._team_names_match(home_var, fixture_home) and \
                       self._team_names_match(away_var, fixture_away):
                        return self._parse_odds_response(fixture)
        
        return None
    
    def _team_names_match(self, name1: str, name2: str) -> bool:
        """Check if two team names match (flexible matching)."""
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()
        
        # Exact match
        if n1 == n2:
            return True
        
        # One contains the other (handles "Man United" vs "Manchester United")
        if n1 in n2 or n2 in n1:
            # But avoid false positives (e.g., "Man" matching "Manchester" alone)
            if len(n1) >= 4 and len(n2) >= 4:
                return True
        
        # Check if key words match (handles "Wolves" vs "Wolverhampton Wanderers")
        n1_words = set(w for w in n1.split() if len(w) >= 3)
        n2_words = set(w for w in n2.split() if len(w) >= 3)
        
        if n1_words and n2_words:
            # If they share at least one significant word
            if n1_words.intersection(n2_words):
                return True
        
        # Check last word match (handles "Brighton" vs "Brighton & Hove Albion")
        n1_last = n1.split()[-1] if n1.split() else ""
        n2_last = n2.split()[-1] if n2.split() else ""
        
        if n1_last and n2_last and len(n1_last) >= 4 and n1_last == n2_last:
            return True
        
        return False
    
    def _parse_odds_response(self, fixture_data: Dict) -> Dict:
        """
        Parse odds response and extract relevant markets.
        Aggregates odds across multiple bookmakers for better accuracy.
        
        Returns:
            Dictionary with:
            - home_win_prob: Probability of home team winning
            - away_win_prob: Probability of away team winning
            - draw_prob: Probability of draw
            - btts_prob: Probability of both teams to score
            - over_2_5_prob: Probability of over 2.5 goals
            - under_2_5_prob: Probability of under 2.5 goals
        """
        parsed = {
            "home_win_prob": 0.5,
            "away_win_prob": 0.5,
            "draw_prob": 0.2,
            "btts_prob": 0.5,
            "over_2_5_prob": 0.5,
            "under_2_5_prob": 0.5,
        }
        
        # Parse bookmaker odds
        bookmakers = fixture_data.get("bookmakers", [])
        if not bookmakers:
            return parsed
        
        # Aggregate odds across ALL bookmakers (Phase 2 enhancement)
        home_win_probs = []
        away_win_probs = []
        draw_probs = []
        btts_probs = []
        over_2_5_probs = []
        under_2_5_probs = []
        
        home_team_name = fixture_data.get("home_team", "").lower()
        away_team_name = fixture_data.get("away_team", "").lower()
        
        for bookmaker in bookmakers:
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
                            if "home" in name or name == home_team_name:
                                home_win_probs.append(prob)
                            elif "away" in name or name == away_team_name:
                                away_win_probs.append(prob)
                            elif "draw" in name:
                                draw_probs.append(prob)
                
                elif market_key == "totals":  # Over/Under totals
                    outcomes = market.get("outcomes", [])
                    for outcome in outcomes:
                        name = outcome.get("name", "").lower()
                        odds = outcome.get("price", 0)
                        if odds > 0:
                            if "over" in name and "2.5" in name:
                                over_2_5_probs.append(1.0 / odds)
                            elif "under" in name and "2.5" in name:
                                under_2_5_probs.append(1.0 / odds)
                
                # Check for BTTS market (if available directly)
                elif market_key == "btts":  # Both teams to score
                    outcomes = market.get("outcomes", [])
                    for outcome in outcomes:
                        name = outcome.get("name", "").lower()
                        odds = outcome.get("price", 0)
                        if odds > 0 and ("yes" in name or "true" in name):
                            btts_probs.append(1.0 / odds)
        
        # Average probabilities across bookmakers (or use median for robustness)
        if home_win_probs:
            parsed["home_win_prob"] = sum(home_win_probs) / len(home_win_probs)
        if away_win_probs:
            parsed["away_win_prob"] = sum(away_win_probs) / len(away_win_probs)
        if draw_probs:
            parsed["draw_prob"] = sum(draw_probs) / len(draw_probs)
        if over_2_5_probs:
            parsed["over_2_5_prob"] = sum(over_2_5_probs) / len(over_2_5_probs)
        if under_2_5_probs:
            parsed["under_2_5_prob"] = sum(under_2_5_probs) / len(under_2_5_probs)
        
        # BTTS: Use direct market if available, otherwise estimate from totals
        if btts_probs:
            # Direct BTTS odds available
            parsed["btts_prob"] = sum(btts_probs) / len(btts_probs)
        else:
            # Estimate BTTS from over/under totals (Phase 2: improved estimation)
            if parsed["over_2_5_prob"] > 0.5:
                # High-scoring game more likely to have BTTS
                parsed["btts_prob"] = min(0.75, parsed["over_2_5_prob"] * 1.15)
            elif parsed["under_2_5_prob"] > 0.6:
                # Low-scoring game less likely to have BTTS
                parsed["btts_prob"] = max(0.25, (1 - parsed["under_2_5_prob"]) * 0.8)
        
        return parsed
    
    def get_player_goalscorer_odds(
        self, 
        player_name: str, 
        fixture_odds: Dict,
        player_stats: Optional[Dict] = None
    ) -> float:
        """
        Get anytime goalscorer odds for a player (Phase 2: Enhanced estimation).
        
        Note: The Odds API doesn't directly provide player-specific goalscorer odds
        in the free tier. This function estimates based on:
        - Team attacking odds (win probability, over/under goals)
        - Player historical performance (goals per game, xG)
        - Player position and premium status
        
        Args:
            player_name: Player name
            fixture_odds: Fixture odds dictionary from get_fixture_odds()
            player_stats: Optional dict with player stats:
                - goals_per_game: Goals per game average
                - xg_per_game: Expected goals per game
                - position: FPL position ID (3=MID, 4=FWD)
                - is_premium: Whether player is premium (price > 9.0)
                
        Returns:
            Estimated probability (0-1) of player scoring
        """
        if not fixture_odds:
            return 0.0
        
        # Phase 2: Use multiple signals for better estimation
        team_win_prob = fixture_odds.get("home_win_prob", 0.5) or fixture_odds.get("away_win_prob", 0.5)
        over_2_5_prob = fixture_odds.get("over_2_5_prob", 0.5)
        
        # Base probability from team attacking potential
        # High-scoring favored teams = more goals for attackers
        attacking_potential = (team_win_prob * 0.6 + over_2_5_prob * 0.4)
        base_prob = attacking_potential * 0.35  # Base multiplier
        
        # Phase 2: Adjust based on player stats if available
        if player_stats:
            goals_per_game = player_stats.get("goals_per_game", 0.0)
            xg_per_game = player_stats.get("xg_per_game", 0.0)
            position = player_stats.get("position", 4)  # Default to FWD
            is_premium = player_stats.get("is_premium", False)
            
            # Players with higher goal rates get boost
            if goals_per_game > 0.5:  # > 0.5 goals per game = elite scorer
                base_prob *= (1.0 + goals_per_game * 0.4)
            elif goals_per_game > 0.3:  # 0.3-0.5 = good scorer
                base_prob *= (1.0 + goals_per_game * 0.25)
            
            # xG is a good predictor (better than actual goals for consistency)
            if xg_per_game > 0.4:  # High xG = getting chances
                base_prob *= (1.0 + xg_per_game * 0.3)
            
            # Premium players tend to be on set pieces, penalties, etc.
            if is_premium:
                base_prob *= 1.2
            
            # FWDs generally score more than MIDs
            if position == 4:  # FWD
                base_prob *= 1.15
            elif position == 3:  # MID
                base_prob *= 0.85
        else:
            # Fallback: Adjust based on player name (premium players)
            premium_players = [
                "haaland", "salah", "kane", "son", "de bruyne", "saka", "martinelli",
                "watkins", "isak", "toney", "jesus", "nunez", "darwin", "gakpo",
                "palmer", "foden", "maddison", "fernandes", "bruno"
            ]
            if any(prem in player_name.lower() for prem in premium_players):
                base_prob *= 1.4
        
        # Bound the probability between 0.05 (5%) and 0.65 (65%)
        return max(0.05, min(0.65, base_prob))
    
    def get_clean_sheet_probability(self, is_home: bool, fixture_odds: Dict) -> float:
        """
        Estimate clean sheet probability for a team (Phase 2: Enhanced).
        
        Uses multiple signals:
        - BTTS probability (inverse relationship)
        - Under 2.5 goals probability (low-scoring games)
        - Team win probability (winning teams more likely to keep clean sheets)
        
        Args:
            is_home: Whether the team is playing at home
            fixture_odds: Fixture odds dictionary
            
        Returns:
            Clean sheet probability (0-1)
        """
        if not fixture_odds:
            return 0.3  # Default estimate
        
        # Phase 2: Use BTTS directly if available (more accurate)
        btts_prob = fixture_odds.get("btts_prob", 0.5)
        # If BTTS is unlikely, clean sheet is more likely
        cs_from_btts = (1.0 - btts_prob) * 0.75
        
        # Use under 2.5 goals as additional signal
        under_2_5_prob = fixture_odds.get("under_2_5_prob", 0.5)
        cs_from_totals = under_2_5_prob * 0.6  # Low-scoring games favor clean sheets
        
        # Team win probability: winning teams often keep clean sheets
        team_win_prob = fixture_odds.get("home_win_prob" if is_home else "away_win_prob", 0.5)
        cs_from_win = team_win_prob * 0.45
        
        # Phase 2: Weighted average of all signals
        cs_prob = (cs_from_btts * 0.4 + cs_from_totals * 0.35 + cs_from_win * 0.25)
        
        # Adjust for home advantage (teams tend to be better defensively at home)
        if is_home:
            cs_prob *= 1.08
        
        return max(0.08, min(0.75, cs_prob))  # Bound between 8% and 75%
    
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

