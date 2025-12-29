"""
Triple Captain Optimizer

Identifies optimal gameweeks to use the Triple Captain chip by calculating
haul probability (15+ points) for players across upcoming gameweeks.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

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
                    
                    # Get clean sheet probability (for DEF/GK)
                    clean_sheet_prob = self._get_clean_sheet_probability(
                        player.element_type, player.team, fixture, features
                    )
                    
                    # Calculate haul probability
                    haul_result = self.haul_calculator.calculate_haul_probability(
                        xg=features.xG,
                        xa=features.xA,
                        position=player.element_type,
                        fixture_difficulty=difficulty,
                        is_home=is_home,
                        clean_sheet_prob=clean_sheet_prob,
                        bonus_points_base=features.ict_index / 10.0,  # Rough BPS proxy
                        is_double_gameweek=is_dgw
                    )
                    
                    player_recommendations.append({
                        "gameweek": gw,
                        "haul_probability": haul_result["haul_probability"],
                        "expected_points": haul_result["expected_points"],
                        "is_double_gameweek": is_dgw,
                        "fixture_difficulty": difficulty,
                        "is_home": is_home,
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
                    "price": player.price / 10.0,
                    "form": float(player.form),
                    "peak_haul_probability": best_gw["haul_probability"],
                    "peak_gameweek": best_gw["gameweek"],
                    "peak_expected_points": best_gw["expected_points"],
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

