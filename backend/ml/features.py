"""
Feature Engineering for FPL Player Points Prediction

Extract and engineer features from FPL data for ML model training.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PlayerFeatures:
    """Features for a single player prediction."""
    player_id: int
    player_name: str
    team_id: int
    position: int
    price: float
    
    # Form features
    form: float
    points_per_game: float
    minutes_percent: float  # % of possible minutes played
    
    # Season stats
    total_points: int
    goals: int
    assists: int
    clean_sheets: int
    bonus: int
    
    # ICT Index
    influence: float
    creativity: float
    threat: float
    ict_index: float
    
    # Expected stats
    xG: float
    xA: float
    xGI: float
    xGC: float
    
    # Ownership and transfers
    ownership: float
    transfers_in: int
    transfers_out: int
    transfer_balance: int
    
    # Fixture difficulty
    next_fixture_difficulty: int
    avg_fixture_difficulty_3: float
    avg_fixture_difficulty_5: float
    
    # Home/Away
    is_home: bool
    
    # Status
    availability: float  # 0-1, chance of playing
    
    # Rolling averages
    avg_points_3: float
    avg_points_5: float
    avg_minutes_3: float
    
    # Betting odds (probabilities, 0-1)
    anytime_goalscorer_prob: float = 0.0  # For FWD/MID
    clean_sheet_prob: float = 0.0  # For DEF/GK
    team_win_prob: float = 0.5  # Team's win probability
    btts_prob: float = 0.5  # Both teams to score probability
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for model input."""
        return {
            "player_id": self.player_id,
            "team_id": self.team_id,
            "position": self.position,
            "price": self.price,
            "form": self.form,
            "points_per_game": self.points_per_game,
            "minutes_percent": self.minutes_percent,
            "total_points": self.total_points,
            "goals": self.goals,
            "assists": self.assists,
            "clean_sheets": self.clean_sheets,
            "bonus": self.bonus,
            "influence": self.influence,
            "creativity": self.creativity,
            "threat": self.threat,
            "ict_index": self.ict_index,
            "xG": self.xG,
            "xA": self.xA,
            "xGI": self.xGI,
            "xGC": self.xGC,
            "ownership": self.ownership,
            "transfers_in": self.transfers_in,
            "transfers_out": self.transfers_out,
            "transfer_balance": self.transfer_balance,
            "next_fixture_difficulty": self.next_fixture_difficulty,
            "avg_fixture_difficulty_3": self.avg_fixture_difficulty_3,
            "avg_fixture_difficulty_5": self.avg_fixture_difficulty_5,
            "is_home": int(self.is_home),
            "availability": self.availability,
            "avg_points_3": self.avg_points_3,
            "avg_points_5": self.avg_points_5,
            "avg_minutes_3": self.avg_minutes_3,
        }
    
    @property
    def feature_vector(self) -> List[float]:
        """Get feature vector for model (excluding IDs)."""
        return [
            self.position,
            self.price,
            self.form,
            self.points_per_game,
            self.minutes_percent,
            self.total_points,
            self.goals,
            self.assists,
            self.clean_sheets,
            self.bonus,
            self.influence,
            self.creativity,
            self.threat,
            self.ict_index,
            self.xG,
            self.xA,
            self.xGI,
            self.xGC,
            self.ownership,
            self.transfer_balance,
            self.next_fixture_difficulty,
            self.avg_fixture_difficulty_3,
            self.avg_fixture_difficulty_5,
            int(self.is_home),
            self.availability,
            self.avg_points_3,
            self.avg_points_5,
            self.avg_minutes_3,
        ]


class FeatureEngineer:
    """Engineer features from FPL data."""
    
    FEATURE_NAMES = [
        "position", "price", "form", "points_per_game", "minutes_percent",
        "total_points", "goals", "assists", "clean_sheets", "bonus",
        "influence", "creativity", "threat", "ict_index",
        "xG", "xA", "xGI", "xGC",
        "ownership", "transfer_balance",
        "next_fixture_difficulty", "avg_fixture_difficulty_3", "avg_fixture_difficulty_5",
        "is_home", "availability",
        "avg_points_3", "avg_points_5", "avg_minutes_3"
    ]
    
    def __init__(self, fpl_client):
        """
        Initialize feature engineer.
        
        Args:
            fpl_client: FPLClient instance
        """
        self.client = fpl_client
        self._teams_dict: Dict[int, Any] = {}
        self._fixtures_dict: Dict[int, List[Any]] = {}
    
    def _load_reference_data(self) -> None:
        """Load teams and fixtures for reference."""
        teams = self.client.get_teams()
        self._teams_dict = {t.id: t for t in teams}
        
        fixtures = self.client.get_fixtures()
        for f in fixtures:
            if f.team_h not in self._fixtures_dict:
                self._fixtures_dict[f.team_h] = []
            if f.team_a not in self._fixtures_dict:
                self._fixtures_dict[f.team_a] = []
            self._fixtures_dict[f.team_h].append(f)
            self._fixtures_dict[f.team_a].append(f)
    
    def _get_fixture_difficulty(
        self,
        team_id: int,
        gameweek: int,
        num_fixtures: int = 1
    ) -> Tuple[int, bool]:
        """
        Get fixture difficulty for a team.
        
        Returns:
            Tuple of (difficulty, is_home)
        """
        if team_id not in self._fixtures_dict:
            return (3, True)  # Default medium difficulty, home
        
        team_fixtures = [
            f for f in self._fixtures_dict[team_id]
            if f.event and f.event >= gameweek
        ]
        team_fixtures.sort(key=lambda f: f.event or 999)
        
        if not team_fixtures:
            return (3, True)
        
        fixture = team_fixtures[0]
        is_home = fixture.team_h == team_id
        difficulty = fixture.team_h_difficulty if is_home else fixture.team_a_difficulty
        
        return (difficulty, is_home)
    
    def _get_avg_fixture_difficulty(
        self,
        team_id: int,
        gameweek: int,
        num_fixtures: int
    ) -> float:
        """Get average fixture difficulty for upcoming fixtures."""
        if team_id not in self._fixtures_dict:
            return 3.0
        
        team_fixtures = [
            f for f in self._fixtures_dict[team_id]
            if f.event and f.event >= gameweek
        ]
        team_fixtures.sort(key=lambda f: f.event or 999)
        team_fixtures = team_fixtures[:num_fixtures]
        
        if not team_fixtures:
            return 3.0
        
        difficulties = []
        for fixture in team_fixtures:
            is_home = fixture.team_h == team_id
            diff = fixture.team_h_difficulty if is_home else fixture.team_a_difficulty
            difficulties.append(diff)
        
        return np.mean(difficulties)
    
    def _get_player_history(self, player_id: int) -> List[Dict[str, Any]]:
        """Get player's gameweek history."""
        try:
            details = self.client.get_player_details(player_id)
            return details.get("history", [])
        except Exception as e:
            logger.warning(f"Could not get history for player {player_id}: {e}")
            return []
    
    def _calculate_rolling_average(
        self,
        history: List[Dict[str, Any]],
        field: str,
        window: int
    ) -> float:
        """Calculate rolling average from history."""
        if not history:
            return 0.0
        
        values = [h.get(field, 0) for h in history[-window:]]
        return np.mean(values) if values else 0.0
    
    def extract_features(
        self,
        player_id: int,
        gameweek: Optional[int] = None,
        include_history: bool = True
    ) -> PlayerFeatures:
        """
        Extract features for a single player.
        
        Args:
            player_id: Player ID
            gameweek: Target gameweek (defaults to next)
            include_history: Whether to fetch detailed history
            
        Returns:
            PlayerFeatures object
        """
        # Load reference data if needed
        if not self._teams_dict:
            self._load_reference_data()
        
        # Get player data
        player = self.client.get_player(player_id)
        if not player:
            raise ValueError(f"Player {player_id} not found")
        
        # Get gameweek
        if gameweek is None:
            next_gw = self.client.get_next_gameweek()
            gameweek = next_gw.id if next_gw else 1
        
        # Get fixture info
        difficulty, is_home = self._get_fixture_difficulty(player.team, gameweek)
        avg_diff_3 = self._get_avg_fixture_difficulty(player.team, gameweek, 3)
        avg_diff_5 = self._get_avg_fixture_difficulty(player.team, gameweek, 5)
        
        # Calculate minutes percentage (assuming 90 * gameweeks played)
        max_minutes = 90 * max(1, gameweek - 1)
        minutes_percent = min(1.0, player.minutes / max_minutes) if max_minutes > 0 else 0
        
        # Get rolling averages from history
        avg_points_3 = 0.0
        avg_points_5 = 0.0
        avg_minutes_3 = 0.0
        
        if include_history:
            history = self._get_player_history(player_id)
            if history:
                avg_points_3 = self._calculate_rolling_average(history, "total_points", 3)
                avg_points_5 = self._calculate_rolling_average(history, "total_points", 5)
                avg_minutes_3 = self._calculate_rolling_average(history, "minutes", 3)
        
        # Availability
        availability = (player.chance_of_playing_next_round or 100) / 100.0
        if player.status != "a":
            availability = 0.0
        
        return PlayerFeatures(
            player_id=player.id,
            player_name=player.web_name,
            team_id=player.team,
            position=player.element_type,
            price=player.price,
            form=float(player.form),
            points_per_game=float(player.points_per_game),
            minutes_percent=minutes_percent,
            total_points=player.total_points,
            goals=player.goals_scored,
            assists=player.assists,
            clean_sheets=player.clean_sheets,
            bonus=player.bonus,
            influence=float(player.influence),
            creativity=float(player.creativity),
            threat=float(player.threat),
            ict_index=float(player.ict_index),
            xG=float(player.expected_goals),
            xA=float(player.expected_assists),
            xGI=float(player.expected_goal_involvements),
            xGC=float(player.expected_goals_conceded),
            ownership=float(player.selected_by_percent),
            transfers_in=player.transfers_in_event,
            transfers_out=player.transfers_out_event,
            transfer_balance=player.transfers_in_event - player.transfers_out_event,
            next_fixture_difficulty=difficulty,
            avg_fixture_difficulty_3=avg_diff_3,
            avg_fixture_difficulty_5=avg_diff_5,
            is_home=is_home,
            availability=availability,
            avg_points_3=avg_points_3,
            avg_points_5=avg_points_5,
            avg_minutes_3=avg_minutes_3,
        )
    
    def extract_all_features(
        self,
        gameweek: Optional[int] = None,
        include_history: bool = False,
        min_minutes: int = 0
    ) -> List[PlayerFeatures]:
        """
        Extract features for all players.
        
        Args:
            gameweek: Target gameweek
            include_history: Whether to fetch detailed history (slow)
            min_minutes: Minimum minutes filter
            
        Returns:
            List of PlayerFeatures
        """
        players = self.client.get_players()
        
        if min_minutes > 0:
            players = [p for p in players if p.minutes >= min_minutes]
        
        features = []
        for i, player in enumerate(players):
            try:
                pf = self.extract_features(
                    player.id,
                    gameweek=gameweek,
                    include_history=include_history
                )
                features.append(pf)
                
                if (i + 1) % 50 == 0:
                    logger.info(f"Processed {i + 1}/{len(players)} players")
                    
            except Exception as e:
                logger.warning(f"Failed to extract features for {player.web_name}: {e}")
        
        return features
    
    def features_to_matrix(
        self,
        features: List[PlayerFeatures]
    ) -> Tuple[np.ndarray, List[int]]:
        """
        Convert list of PlayerFeatures to numpy matrix.
        
        Returns:
            Tuple of (feature_matrix, player_ids)
        """
        if not features:
            return np.array([]), []
        
        matrix = np.array([f.feature_vector for f in features])
        player_ids = [f.player_id for f in features]
        
        return matrix, player_ids


