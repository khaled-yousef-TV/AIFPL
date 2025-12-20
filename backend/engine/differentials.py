"""
Differential Finder

Identify low-ownership players with high predicted points for rank climbing.
"""

import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Differential:
    """A differential pick."""
    player_id: int
    name: str
    team: str
    position: str
    price: float
    predicted_points: float
    ownership: float
    form: float
    upcoming_fixtures: List[str]
    reasoning: str
    risk_level: str  # "low", "medium", "high"


class DifferentialFinder:
    """
    Find differential picks for rank climbing.
    
    Differentials are low-ownership players who could score well,
    allowing you to gain rank when they haul.
    """
    
    POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    
    def __init__(
        self,
        max_ownership: float = 10.0,
        min_predicted: float = 4.0,
        min_form: float = 3.0
    ):
        """
        Initialize differential finder.
        
        Args:
            max_ownership: Maximum ownership % for differential
            min_predicted: Minimum predicted points
            min_form: Minimum form rating
        """
        self.max_ownership = max_ownership
        self.min_predicted = min_predicted
        self.min_form = min_form
    
    def find_differentials(
        self,
        all_predictions: List[Tuple[int, str, float]],  # id, name, predicted
        player_data: Dict[int, Dict],  # Full player data
        team_names: Dict[int, str],
        fixture_data: Optional[Dict[int, List[str]]] = None,
        top_n: int = 10
    ) -> List[Differential]:
        """
        Find top differential picks.
        
        Args:
            all_predictions: Predictions for all players
            player_data: Dictionary of player details
            team_names: Team ID to name mapping
            fixture_data: Optional upcoming fixtures by team
            top_n: Number of differentials to return
            
        Returns:
            List of Differential picks
        """
        differentials = []
        
        for player_id, name, predicted in all_predictions:
            data = player_data.get(player_id, {})
            
            # Get ownership
            ownership = data.get("selected_by_percent", 0)
            if isinstance(ownership, str):
                ownership = float(ownership)
            
            # Filter by ownership
            if ownership > self.max_ownership:
                continue
            
            # Filter by predicted points
            if predicted < self.min_predicted:
                continue
            
            # Filter by form
            form = data.get("form", 0)
            if isinstance(form, str):
                form = float(form)
            if form < self.min_form:
                continue
            
            # Get additional info
            team_id = data.get("team", 0)
            team_name = team_names.get(team_id, "Unknown")
            position = self.POSITION_NAMES.get(data.get("element_type", 3), "MID")
            price = data.get("now_cost", 0) / 10
            
            # Get fixtures
            fixtures = fixture_data.get(team_id, []) if fixture_data else []
            
            # Calculate risk level
            risk = self._calculate_risk(data, predicted, ownership)
            
            # Build reasoning
            reasoning = self._build_reasoning(
                name, predicted, ownership, form, fixtures
            )
            
            differentials.append(Differential(
                player_id=player_id,
                name=name,
                team=team_name,
                position=position,
                price=price,
                predicted_points=predicted,
                ownership=ownership,
                form=form,
                upcoming_fixtures=fixtures[:5],
                reasoning=reasoning,
                risk_level=risk
            ))
        
        # Sort by predicted points (best differentials first)
        differentials.sort(key=lambda d: d.predicted_points, reverse=True)
        
        return differentials[:top_n]
    
    def _calculate_risk(
        self,
        player_data: Dict,
        predicted: float,
        ownership: float
    ) -> str:
        """Calculate risk level of differential pick."""
        # Factors that increase risk:
        # - Very low ownership (might be for a reason)
        # - Low minutes
        # - Inconsistent returns
        # - Injury doubt
        
        risk_score = 0
        
        # Very low ownership
        if ownership < 2.0:
            risk_score += 2
        elif ownership < 5.0:
            risk_score += 1
        
        # Minutes
        minutes = player_data.get("minutes", 0)
        if minutes < 500:
            risk_score += 2
        elif minutes < 1000:
            risk_score += 1
        
        # Injury risk
        chance = player_data.get("chance_of_playing_next_round", 100)
        if chance is not None and chance < 100:
            risk_score += 2
        
        # Convert to level
        if risk_score <= 1:
            return "low"
        elif risk_score <= 3:
            return "medium"
        else:
            return "high"
    
    def _build_reasoning(
        self,
        name: str,
        predicted: float,
        ownership: float,
        form: float,
        fixtures: List[str]
    ) -> str:
        """Build reasoning string for differential pick."""
        parts = []
        
        parts.append(f"{predicted:.1f} predicted points")
        parts.append(f"only {ownership:.1f}% owned")
        parts.append(f"form: {form:.1f}")
        
        if fixtures:
            parts.append(f"next: {', '.join(fixtures[:3])}")
        
        return ". ".join(parts)
    
    def find_template_differentials(
        self,
        all_predictions: List[Tuple[int, str, float]],
        player_data: Dict[int, Dict],
        template_ids: List[int],
        team_names: Dict[int, str]
    ) -> List[Differential]:
        """
        Find differentials from players NOT in the template team.
        
        The "template" is the most commonly owned set of players.
        Finding alternatives to template players can provide rank upside.
        
        Args:
            all_predictions: All player predictions
            player_data: Player details
            template_ids: IDs of template players
            team_names: Team ID to name mapping
            
        Returns:
            Non-template differentials
        """
        # Filter to non-template players only
        non_template = [
            (pid, name, pred)
            for pid, name, pred in all_predictions
            if pid not in template_ids
        ]
        
        return self.find_differentials(
            non_template,
            player_data,
            team_names
        )
    
    def get_position_differentials(
        self,
        differentials: List[Differential],
        position: str
    ) -> List[Differential]:
        """Get differentials for a specific position."""
        return [d for d in differentials if d.position == position]
    
    def calculate_differential_score(
        self,
        predicted_points: float,
        ownership: float
    ) -> float:
        """
        Calculate a differential score.
        
        Higher score = better differential value
        (high predicted, low ownership)
        """
        if ownership <= 0:
            ownership = 0.1
        
        # Score = predicted points / sqrt(ownership)
        # This rewards high points and low ownership
        return predicted_points / (ownership ** 0.5)


