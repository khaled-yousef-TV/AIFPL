"""
Captain Selection Logic

Pick captain and vice-captain based on predicted points.
"""

import logging
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CaptainPick:
    """Captain selection result."""
    captain_id: int
    captain_name: str
    captain_predicted: float
    vice_captain_id: int
    vice_captain_name: str
    vice_captain_predicted: float
    reasoning: str


class CaptainPicker:
    """Select captain and vice-captain for the gameweek."""
    
    def __init__(
        self,
        differential_threshold: float = 15.0,
        min_predicted_points: float = 4.0
    ):
        """
        Initialize captain picker.
        
        Args:
            differential_threshold: Max ownership % to consider as differential
            min_predicted_points: Minimum predicted points to consider
        """
        self.differential_threshold = differential_threshold
        self.min_predicted_points = min_predicted_points
    
    def pick(
        self,
        team_predictions: List[Tuple[int, str, float]],
        player_ownership: Dict[int, float],
        prefer_differential: bool = False,
        starting_xi_ids: Optional[List[int]] = None
    ) -> CaptainPick:
        """
        Pick captain and vice-captain.
        
        Args:
            team_predictions: List of (player_id, name, predicted_points) for team
            player_ownership: Dict of player_id -> ownership %
            prefer_differential: Whether to prefer low-ownership captain
            starting_xi_ids: If provided, only pick from these players
            
        Returns:
            CaptainPick with selections
        """
        if not team_predictions:
            raise ValueError("No predictions provided")
        
        # Filter to starting XI if provided
        candidates = team_predictions
        if starting_xi_ids:
            candidates = [
                (pid, name, pred)
                for pid, name, pred in team_predictions
                if pid in starting_xi_ids
            ]
        
        if not candidates:
            candidates = team_predictions
        
        # Sort by predicted points
        candidates.sort(key=lambda x: x[2], reverse=True)
        
        # Filter by minimum points
        viable = [
            (pid, name, pred)
            for pid, name, pred in candidates
            if pred >= self.min_predicted_points
        ]
        
        if not viable:
            viable = candidates[:2]  # Take top 2 anyway
        
        reasoning_parts = []
        
        # Default: pick highest predicted
        captain_id, captain_name, captain_pred = viable[0]
        reasoning_parts.append(f"{captain_name} has highest predicted points ({captain_pred:.1f})")
        
        # Check for differential option
        if prefer_differential:
            differentials = [
                (pid, name, pred)
                for pid, name, pred in viable
                if player_ownership.get(pid, 100) < self.differential_threshold
            ]
            
            if differentials:
                # Pick highest predicted differential
                diff_pick = differentials[0]
                ownership = player_ownership.get(diff_pick[0], 0)
                
                # Only override if differential is close enough to top pick
                points_diff = captain_pred - diff_pick[2]
                if points_diff < 1.5:  # Within 1.5 points
                    captain_id, captain_name, captain_pred = diff_pick
                    reasoning_parts.append(
                        f"Differential pick: {captain_name} ({ownership:.1f}% owned) "
                        f"is close in prediction and offers rank upside"
                    )
        
        # Vice captain: second highest (excluding captain)
        vc_candidates = [
            (pid, name, pred)
            for pid, name, pred in viable
            if pid != captain_id
        ]
        
        if vc_candidates:
            vc_id, vc_name, vc_pred = vc_candidates[0]
        else:
            # Fallback to captain if only one viable player
            vc_id, vc_name, vc_pred = captain_id, captain_name, captain_pred
        
        reasoning_parts.append(f"Vice-captain: {vc_name} ({vc_pred:.1f} predicted)")
        
        return CaptainPick(
            captain_id=captain_id,
            captain_name=captain_name,
            captain_predicted=captain_pred,
            vice_captain_id=vc_id,
            vice_captain_name=vc_name,
            vice_captain_predicted=vc_pred,
            reasoning=". ".join(reasoning_parts)
        )
    
    def get_captain_options(
        self,
        team_predictions: List[Tuple[int, str, float]],
        player_ownership: Dict[int, float],
        top_n: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Get top captain options with analysis.
        
        Returns:
            List of captain options with stats
        """
        candidates = sorted(team_predictions, key=lambda x: x[2], reverse=True)[:top_n]
        
        options = []
        for pid, name, pred in candidates:
            ownership = player_ownership.get(pid, 0)
            
            # Calculate effective ownership if captained
            # (Approximate - assumes similar ownership as captain)
            eo_estimate = ownership * 1.5  # Rough estimate
            
            options.append({
                "player_id": pid,
                "name": name,
                "predicted_points": round(pred, 2),
                "predicted_captain_points": round(pred * 2, 2),
                "ownership": round(ownership, 1),
                "effective_ownership_estimate": round(eo_estimate, 1),
                "is_differential": ownership < self.differential_threshold,
            })
        
        return options

