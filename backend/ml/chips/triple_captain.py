"""
Triple Captain Optimizer

Identifies optimal gameweeks to use the Triple Captain chip by calculating
haul probability (15+ points) for players across upcoming gameweeks.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import numpy as np

from ..features import FeatureEngineer, PlayerFeatures
from .haul_probability import HaulProbabilityCalculator

logger = logging.getLogger(__name__)


class TripleCaptainOptimizer:
    """Optimize Triple Captain chip usage."""
    
    def __init__(self, fpl_client, feature_engineer: FeatureEngineer):
        """
        Initialize the Triple Captain optimizer.
        
        Args:
            fpl_client: FPLClient instance
            feature_engineer: FeatureEngineer instance
        """
        self.client = fpl_client
        self.feature_engineer = feature_engineer
        self.haul_calculator = HaulProbabilityCalculator()
    
    def get_triple_captain_recommendations(
        self,
        gameweek_range: int = 5,
        top_n: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get Triple Captain recommendations for upcoming gameweeks.
        
        Args:
            gameweek_range: Number of gameweeks to analyze (default: 5)
            top_n: Number of top recommendations to return
            
        Returns:
            List of recommendations sorted by peak haul probability
        """
        current_gw = self._get_current_gameweek()
        if not current_gw:
            logger.error("Could not determine current gameweek")
            return []
        
        next_gw = current_gw + 1
        end_gw = next_gw + gameweek_range - 1
        
        logger.info(f"Analyzing Triple Captain options for GW{next_gw}-{end_gw}")
        
        # Get all players
        all_players = self.client.get_players()
        if not all_players:
            logger.error("Could not fetch players")
            return []
        
        # Filter players early to reduce computation:
        # 1. Only available players
        # 2. Minimum form threshold (>= 2.0) or minimum total points (>= 20)
        # 3. Limit to top 200 by form/points to avoid processing all 700+ players
        filtered_players = [
            p for p in all_players
            if p.status == "a" and (float(p.form) >= 2.0 or p.total_points >= 20)
        ]
        
        # Sort by form (descending) and take top 200
        filtered_players.sort(key=lambda p: float(p.form), reverse=True)
        players = filtered_players[:200]
        
        logger.info(f"Processing {len(players)} players (filtered from {len(all_players)} total)")
        
        # Get fixtures for double gameweek detection
        fixtures_by_gw = self._get_fixtures_by_gameweek(next_gw, end_gw)
        
        recommendations = []
        processed = 0
        
        for player in players:
            processed += 1
            if processed % 50 == 0:
                logger.info(f"Processed {processed}/{len(players)} players...")
            try:
                # Skip players with low availability
                if player.status != "a":  # Only available players
                    continue
                
                # Get player features for current gameweek
                features = self.feature_engineer.extract_features(
                    player.id, gameweek=current_gw
                )
                
                if not features:
                    continue
                
                # PHASE 1 FIX: Use 4-6 week rolling window (EWMA) instead of season totals
                # This ensures recent poor form (like Malen) is properly reflected
                recent_xg, recent_xa = self._calculate_recent_xg_xa(player.id, current_gw)
                
                # Fallback to season average if no recent data
                if recent_xg == 0.0 and recent_xa == 0.0:
                    games_played = max(1.0, player.minutes / 90.0)
                    recent_xg = features.xG / games_played if games_played > 0 else 0.0
                    recent_xa = features.xA / games_played if games_played > 0 else 0.0
                
                # PHASE 1 FIX: Calculate probability of starting based on recent starts
                start_probability = self._calculate_start_probability(player.id, current_gw)
                
                # Analyze each gameweek in range
                player_recommendations = []
                
                for gw in range(next_gw, end_gw + 1):
                    # Check if player has fixture(s) this gameweek
                    player_fixtures = self._get_player_fixtures(
                        player.id, player.team, gw, fixtures_by_gw
                    )
                    
                    if not player_fixtures:
                        continue
                    
                    is_dgw = len(player_fixtures) > 1
                    
                    # Get fixture difficulty for this gameweek
                    fixture = player_fixtures[0]
                    is_home = fixture.team_h == player.team
                    difficulty = (
                        fixture.team_h_difficulty if is_home
                        else fixture.team_a_difficulty
                    )
                    
                    # Get opponent team
                    opponent_team_id = fixture.team_a if is_home else fixture.team_h
                    opponent_team_name = self._get_team_name(opponent_team_id)
                    
                    # For DGW, get both opponents
                    opponents = [opponent_team_name]
                    if is_dgw and len(player_fixtures) > 1:
                        fixture2 = player_fixtures[1]
                        is_home2 = fixture2.team_h == player.team
                        opponent_team_id2 = fixture2.team_a if is_home2 else fixture2.team_h
                        opponent_team_name2 = self._get_team_name(opponent_team_id2)
                        opponents.append(opponent_team_name2)
                    
                    # Get clean sheet probability (for DEF/GK)
                    clean_sheet_prob = self._get_clean_sheet_probability(
                        player.element_type, player.team, fixture, features
                    )
                    
                    # Calculate haul probability using RECENT xG/xA (4-6 week window)
                    haul_result = self.haul_calculator.calculate_haul_probability(
                        xg=recent_xg,  # Recent per-game xG from 4-6 week window
                        xa=recent_xa,  # Recent per-game xA from 4-6 week window
                        position=player.element_type,
                        fixture_difficulty=difficulty,
                        is_home=is_home,
                        clean_sheet_prob=clean_sheet_prob,
                        bonus_points_base=features.ict_index / 10.0,  # Rough BPS proxy
                        is_double_gameweek=is_dgw,
                        start_probability=start_probability  # Probability of starting
                    )
                    
                    player_recommendations.append({
                        "gameweek": gw,
                        "haul_probability": haul_result["haul_probability"],
                        "expected_points": haul_result["expected_points"],
                        "is_double_gameweek": is_dgw,
                        "fixture_difficulty": difficulty,
                        "is_home": is_home,
                        "opponent": opponent_team_name if not is_dgw else " / ".join(opponents),
                        "opponents": opponents,  # Array for DGW
                        "statistics": {
                            "median_points": haul_result["median_points"],
                            "p75_points": haul_result["p75_points"],
                            "p90_points": haul_result["p90_points"],
                        }
                    })
                
                if not player_recommendations:
                    continue
                
                # Find peak haul probability (best gameweek for this player)
                best_gw = max(
                    player_recommendations,
                    key=lambda x: x["haul_probability"]
                )
                
                recommendations.append({
                    "player_id": player.id,
                    "player_name": player.web_name,
                    "full_name": player.full_name,
                    "team": self._get_team_name(player.team),
                    "position": self._get_position_name(player.element_type),
                    "price": player.price,  # Already in millions (from Player.price property)
                    "form": float(player.form),
                    "peak_haul_probability": best_gw["haul_probability"],
                    "peak_gameweek": best_gw["gameweek"],
                    "peak_expected_points": best_gw["expected_points"],
                    "peak_opponent": best_gw.get("opponent", ""),
                    "is_double_gameweek": best_gw["is_double_gameweek"],
                    "all_gameweeks": player_recommendations,
                })
                
            except Exception as e:
                logger.warning(f"Error processing player {player.web_name}: {e}")
                continue
        
        # Sort by peak haul probability
        recommendations.sort(
            key=lambda x: x["peak_haul_probability"],
            reverse=True
        )
        
        return recommendations[:top_n]
    
    def _get_current_gameweek(self) -> Optional[int]:
        """Get current gameweek number."""
        gw = self.client.get_current_gameweek()
        if gw:
            return gw.id
        return None
    
    def _get_fixtures_by_gameweek(
        self,
        start_gw: int,
        end_gw: int
    ) -> Dict[int, List]:
        """Get fixtures grouped by gameweek."""
        fixtures_by_gw = {}
        
        for gw in range(start_gw, end_gw + 1):
            fixtures = self.client.get_fixtures(gameweek=gw)
            fixtures_by_gw[gw] = fixtures
        
        return fixtures_by_gw
    
    def _get_player_fixtures(
        self,
        player_id: int,
        team_id: int,
        gameweek: int,
        fixtures_by_gw: Dict[int, List]
    ) -> List:
        """Get fixtures for a player in a specific gameweek."""
        fixtures = fixtures_by_gw.get(gameweek, [])
        return [
            f for f in fixtures
            if f.team_h == team_id or f.team_a == team_id
        ]
    
    def _get_clean_sheet_probability(
        self,
        position: int,
        team_id: int,
        fixture,
        features: PlayerFeatures
    ) -> float:
        """
        Estimate clean sheet probability for DEF/GK.
        
        Simplified: based on fixture difficulty and team defensive strength.
        """
        if position not in [1, 2]:  # Only for GK and DEF
            return 0.0
        
        # Base probability from fixture difficulty
        difficulty = (
            fixture.team_h_difficulty if fixture.team_h == team_id
            else fixture.team_a_difficulty
        )
        
        # Easier fixtures = higher clean sheet probability
        base_prob = {
            1: 0.5,  # Very easy
            2: 0.4,  # Easy
            3: 0.3,  # Medium
            4: 0.2,  # Hard
            5: 0.15,  # Very hard
        }.get(difficulty, 0.3)
        
        # Adjust based on player's xGC (expected goals conceded)
        if features.xGC > 0:
            # Lower xGC = better defense = higher CS probability
            adjustment = max(0.5, 1.0 - (features.xGC / 2.0))
            base_prob *= adjustment
        
        return min(base_prob, 0.6)  # Cap at 60%
    
    def _get_team_name(self, team_id: int) -> str:
        """Get team name by ID."""
        team = self.client.get_team(team_id)
        return team.short_name if team else f"Team {team_id}"
    
    def _get_position_name(self, position_id: int) -> str:
        """Get position name by ID."""
        positions = {
            1: "GK",
            2: "DEF",
            3: "MID",
            4: "FWD"
        }
        return positions.get(position_id, "UNK")
    
    def _calculate_recent_xg_xa(
        self,
        player_id: int,
        current_gw: int,
        window_weeks: int = 6,
        use_ewma: bool = True
    ) -> Tuple[float, float]:
        """
        Calculate recent xG/xA using 4-6 week rolling window with EWMA.
        
        Args:
            player_id: Player ID
            current_gw: Current gameweek
            window_weeks: Number of weeks to look back (default: 6)
            use_ewma: Whether to use exponential weighted moving average (default: True)
            
        Returns:
            Tuple of (recent_xg_per_game, recent_xa_per_game)
        """
        try:
            # Get player history
            details = self.client.get_player_details(player_id)
            history = details.get("history", [])
            
            if not history:
                return (0.0, 0.0)
            
            # Filter to recent gameweeks (last window_weeks gameweeks before current)
            recent_history = [
                h for h in history
                if h.get("round") and h.get("round") < current_gw
            ]
            
            # Sort by gameweek (ascending)
            recent_history.sort(key=lambda x: x.get("round", 0))
            
            # Take last window_weeks gameweeks
            recent_history = recent_history[-window_weeks:]
            
            if not recent_history:
                return (0.0, 0.0)
            
            # Extract xG and xA from history
            # FPL history has "expected_goals" and "expected_assists" per gameweek
            xg_values = []
            xa_values = []
            
            for game in recent_history:
                # Safely convert to float, handling None, strings, and invalid values
                xg_raw = game.get("expected_goals", 0.0)
                xa_raw = game.get("expected_assists", 0.0)
                minutes = game.get("minutes", 0)
                
                # Convert to float, defaulting to 0.0 if conversion fails
                try:
                    xg = float(xg_raw) if xg_raw is not None else 0.0
                except (ValueError, TypeError):
                    xg = 0.0
                
                try:
                    xa = float(xa_raw) if xa_raw is not None else 0.0
                except (ValueError, TypeError):
                    xa = 0.0
                
                # Convert minutes to int for comparison
                try:
                    minutes = int(minutes) if minutes is not None else 0
                except (ValueError, TypeError):
                    minutes = 0
                
                # Only include games where player actually played (minutes > 0)
                if minutes > 0:
                    xg_values.append(xg)
                    xa_values.append(xa)
            
            if not xg_values:
                return (0.0, 0.0)
            
            if use_ewma:
                # Exponential Weighted Moving Average (more weight to recent games)
                # Alpha = 0.3 means recent games have ~70% weight, older games ~30%
                alpha = 0.3
                weights = [alpha * ((1 - alpha) ** i) for i in range(len(xg_values) - 1, -1, -1)]
                # Normalize weights
                weight_sum = sum(weights)
                if weight_sum > 0:
                    weights = [w / weight_sum for w in weights]
                
                recent_xg = sum(xg * w for xg, w in zip(xg_values, weights))
                recent_xa = sum(xa * w for xa, w in zip(xa_values, weights))
            else:
                # Simple rolling average
                recent_xg = np.mean(xg_values)
                recent_xa = np.mean(xa_values)
            
            return (recent_xg, recent_xa)
            
        except Exception as e:
            logger.warning(f"Error calculating recent xG/xA for player {player_id}: {e}")
            return (0.0, 0.0)
    
    def _calculate_start_probability(
        self,
        player_id: int,
        current_gw: int,
        lookback_games: int = 3
    ) -> float:
        """
        Calculate probability of starting based on recent starts.
        
        If player has started < 2 of last 3 games, they have lower start probability.
        This will be used in Monte Carlo to simulate bench appearances (1 point).
        
        Args:
            player_id: Player ID
            current_gw: Current gameweek
            lookback_games: Number of recent games to check (default: 3)
            
        Returns:
            Probability of starting (0.0 to 1.0)
        """
        try:
            # Get player history
            details = self.client.get_player_details(player_id)
            history = details.get("history", [])
            
            if not history:
                # No history = assume starter (100% probability)
                return 1.0
            
            # Filter to recent gameweeks before current
            recent_history = [
                h for h in history
                if h.get("round") and h.get("round") < current_gw
            ]
            
            # Sort by gameweek (ascending)
            recent_history.sort(key=lambda x: x.get("round", 0))
            
            # Take last lookback_games gameweeks
            recent_history = recent_history[-lookback_games:]
            
            if not recent_history:
                return 1.0  # No recent data = assume starter
            
            # Count starts (minutes >= 60 is considered a start)
            starts = sum(1 for game in recent_history if game.get("minutes", 0) >= 60)
            
            # If started < 2 of last 3, reduce start probability
            if starts < 2:
                # Linear interpolation: 0 starts = 0.3, 1 start = 0.5, 2+ starts = 1.0
                if starts == 0:
                    return 0.3
                elif starts == 1:
                    return 0.5
                else:
                    return 1.0
            else:
                # Started 2+ of last 3 = regular starter
                return 1.0
                
        except Exception as e:
            logger.warning(f"Error calculating start probability for player {player_id}: {e}")
            # Default to starter if we can't determine
            return 1.0

