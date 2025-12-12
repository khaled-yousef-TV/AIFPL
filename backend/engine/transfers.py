"""
Transfer Decision Engine

Identify underperforming players and suggest optimal transfers.
"""

import logging
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TransferSuggestion:
    """A suggested transfer."""
    player_out_id: int
    player_out_name: str
    player_out_price: float
    player_out_predicted: float
    
    player_in_id: int
    player_in_name: str
    player_in_price: float
    player_in_predicted: float
    
    points_gain: float
    price_change: float
    reasoning: str


@dataclass
class TransferPlan:
    """Complete transfer plan for the gameweek."""
    transfers: List[TransferSuggestion]
    total_points_gain: float
    total_cost: int  # Transfer cost in points (4 per extra transfer)
    net_gain: float
    use_wildcard: bool
    use_freehit: bool
    reasoning: str


class TransferEngine:
    """
    Engine for making transfer decisions.
    
    Considers:
    - Player predicted points
    - Price changes and budget
    - Number of free transfers
    - Injury/suspension status
    """
    
    TRANSFER_COST = 4  # Points per extra transfer
    
    def __init__(
        self,
        min_points_gain: float = 2.0,
        consider_price_rises: bool = True
    ):
        """
        Initialize transfer engine.
        
        Args:
            min_points_gain: Minimum points gain to suggest a transfer
            consider_price_rises: Whether to consider predicted price changes
        """
        self.min_points_gain = min_points_gain
        self.consider_price_rises = consider_price_rises
    
    def suggest_transfers(
        self,
        current_team: List[Tuple[int, str, float, int, float]],  # id, name, price, pos, predicted
        all_players: List[Tuple[int, str, float, int, float]],  # id, name, price, pos, predicted
        budget: float,
        free_transfers: int = 1,
        max_transfers: int = 2,
        unavailable_ids: Optional[Set[int]] = None
    ) -> TransferPlan:
        """
        Suggest transfers for the gameweek.
        
        Args:
            current_team: Current squad (id, name, price, position, predicted)
            all_players: All available players
            budget: Available budget (in bank)
            free_transfers: Number of free transfers
            max_transfers: Maximum transfers to suggest
            unavailable_ids: Players unavailable (injured, etc.)
            
        Returns:
            TransferPlan with suggestions
        """
        unavailable = unavailable_ids or set()
        
        # Get current team IDs
        team_ids = {p[0] for p in current_team}
        
        # Group current team by position
        team_by_pos: Dict[int, List[Tuple]] = {1: [], 2: [], 3: [], 4: []}
        for p in current_team:
            team_by_pos[p[3]].append(p)
        
        # Find potential targets (not in team, available)
        targets = [
            p for p in all_players
            if p[0] not in team_ids and p[0] not in unavailable
        ]
        
        # Group targets by position
        targets_by_pos: Dict[int, List[Tuple]] = {1: [], 2: [], 3: [], 4: []}
        for p in targets:
            targets_by_pos[p[3]].append(p)
        
        # Sort targets by predicted points
        for pos in targets_by_pos:
            targets_by_pos[pos].sort(key=lambda x: x[4], reverse=True)
        
        # Find best transfers
        suggestions = []
        used_out = set()
        used_in = set()
        
        for _ in range(max_transfers):
            best_transfer = None
            best_gain = self.min_points_gain
            
            # Check each current player
            for p_out in current_team:
                if p_out[0] in used_out:
                    continue
                
                pos = p_out[3]
                out_price = p_out[2]
                out_pred = p_out[4]
                
                # Find best replacement at same position
                for p_in in targets_by_pos[pos]:
                    if p_in[0] in used_in:
                        continue
                    
                    in_price = p_in[2]
                    in_pred = p_in[4]
                    
                    # Check budget
                    price_diff = in_price - out_price
                    if price_diff > budget:
                        continue
                    
                    # Calculate gain
                    points_gain = in_pred - out_pred
                    
                    if points_gain > best_gain:
                        best_gain = points_gain
                        best_transfer = TransferSuggestion(
                            player_out_id=p_out[0],
                            player_out_name=p_out[1],
                            player_out_price=out_price,
                            player_out_predicted=out_pred,
                            player_in_id=p_in[0],
                            player_in_name=p_in[1],
                            player_in_price=in_price,
                            player_in_predicted=in_pred,
                            points_gain=points_gain,
                            price_change=price_diff,
                            reasoning=f"+{points_gain:.1f} predicted points"
                        )
            
            if best_transfer:
                suggestions.append(best_transfer)
                used_out.add(best_transfer.player_out_id)
                used_in.add(best_transfer.player_in_id)
                budget -= best_transfer.price_change
            else:
                break
        
        # Calculate costs
        n_transfers = len(suggestions)
        transfer_cost = max(0, n_transfers - free_transfers) * self.TRANSFER_COST
        
        total_gain = sum(t.points_gain for t in suggestions)
        net_gain = total_gain - transfer_cost
        
        # Build reasoning
        reasoning_parts = []
        if suggestions:
            reasoning_parts.append(f"{n_transfers} transfer(s) suggested")
            if transfer_cost > 0:
                reasoning_parts.append(f"Transfer cost: -{transfer_cost} points")
            reasoning_parts.append(f"Net gain: {net_gain:.1f} points")
        else:
            reasoning_parts.append("No beneficial transfers found")
        
        return TransferPlan(
            transfers=suggestions,
            total_points_gain=total_gain,
            total_cost=transfer_cost,
            net_gain=net_gain,
            use_wildcard=False,
            use_freehit=False,
            reasoning=". ".join(reasoning_parts)
        )
    
    def find_urgent_transfers(
        self,
        current_team: List[Tuple[int, str, float, int, float]],
        player_status: Dict[int, str],  # id -> status (a/d/i/s/u)
        player_news: Dict[int, str]
    ) -> List[int]:
        """
        Find players who urgently need transferring out.
        
        Returns list of player IDs that should be transferred.
        """
        urgent = []
        
        for p_id, name, price, pos, pred in current_team:
            status = player_status.get(p_id, "a")
            news = player_news.get(p_id, "")
            
            # Injured or suspended = urgent
            if status in ["i", "s"]:
                urgent.append(p_id)
                logger.info(f"Urgent: {name} is {status} - {news}")
            
            # Doubtful with low chance = urgent
            elif status == "d" and "25%" in news:
                urgent.append(p_id)
                logger.info(f"Urgent: {name} only 25% chance - {news}")
        
        return urgent
    
    def should_use_wildcard(
        self,
        current_predictions: List[float],
        optimal_predictions: List[float],
        weeks_until_wildcard_expires: int = 20
    ) -> Tuple[bool, str]:
        """
        Decide if wildcard should be used.
        
        Args:
            current_predictions: Sum of predictions for current team
            optimal_predictions: Sum of predictions for optimal team
            weeks_until_wildcard_expires: Weeks until WC deadline
            
        Returns:
            Tuple of (should_use, reasoning)
        """
        current_total = sum(current_predictions)
        optimal_total = sum(optimal_predictions)
        
        improvement = optimal_total - current_total
        
        # Rough heuristic: use if improvement > 20 points and WC about to expire
        # or if improvement > 40 points any time
        
        if improvement > 40:
            return True, f"Large improvement possible: +{improvement:.1f} predicted points"
        
        if improvement > 20 and weeks_until_wildcard_expires < 5:
            return True, f"Wildcard expiring soon with {improvement:.1f} point improvement"
        
        return False, f"Wildcard not recommended (improvement: {improvement:.1f} points)"

