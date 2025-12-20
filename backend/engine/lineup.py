"""
Lineup Optimizer

Optimize starting XI and bench order based on predicted points.
"""

import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PlayerSlot:
    """A player in a lineup slot."""
    player_id: int
    name: str
    position: int  # 1=GK, 2=DEF, 3=MID, 4=FWD
    predicted_points: float
    is_starter: bool
    bench_order: Optional[int] = None  # 0-3 for bench


@dataclass
class OptimizedLineup:
    """Optimized lineup result."""
    starting_xi: List[PlayerSlot]
    bench: List[PlayerSlot]
    formation: str
    total_predicted_points: float
    reasoning: str


class LineupOptimizer:
    """
    Optimize team lineup based on predictions.
    
    FPL Rules:
    - Must play exactly 11 players
    - Formation constraints: 1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD
    - 4 bench players in order (first sub should be best available)
    """
    
    # Valid formations (DEF-MID-FWD)
    VALID_FORMATIONS = [
        (3, 5, 2), (3, 4, 3),
        (4, 5, 1), (4, 4, 2), (4, 3, 3),
        (5, 4, 1), (5, 3, 2), (5, 2, 3),
    ]
    
    def __init__(self):
        """Initialize lineup optimizer."""
        pass
    
    def optimize(
        self,
        squad_predictions: List[Tuple[int, str, float]],
        player_positions: Dict[int, int],
        player_availability: Optional[Dict[int, float]] = None
    ) -> OptimizedLineup:
        """
        Optimize lineup from 15-player squad.
        
        Args:
            squad_predictions: List of (player_id, name, predicted_points)
            player_positions: Dict of player_id -> position (1-4)
            player_availability: Optional dict of player_id -> availability (0-1)
            
        Returns:
            OptimizedLineup with best 11 and bench order
        """
        if len(squad_predictions) != 15:
            logger.warning(f"Expected 15 players, got {len(squad_predictions)}")
        
        availability = player_availability or {}
        
        # Group players by position
        by_position: Dict[int, List[Tuple[int, str, float]]] = {
            1: [], 2: [], 3: [], 4: []
        }
        
        for pid, name, pred in squad_predictions:
            pos = player_positions.get(pid, 3)  # Default MID
            
            # Adjust prediction by availability
            avail = availability.get(pid, 1.0)
            adj_pred = pred * avail
            
            by_position[pos].append((pid, name, adj_pred, pred))
        
        # Sort each position by adjusted prediction
        for pos in by_position:
            by_position[pos].sort(key=lambda x: x[2], reverse=True)
        
        # Find best formation
        best_lineup = None
        best_total = -1
        best_formation = None
        
        for formation in self.VALID_FORMATIONS:
            n_def, n_mid, n_fwd = formation
            
            # Check if we have enough players
            if (len(by_position[2]) < n_def or
                len(by_position[3]) < n_mid or
                len(by_position[4]) < n_fwd):
                continue
            
            # Select best players for this formation
            lineup = []
            
            # 1 GK
            if by_position[1]:
                lineup.append(by_position[1][0])
            else:
                continue
            
            # DEF
            lineup.extend(by_position[2][:n_def])
            
            # MID
            lineup.extend(by_position[3][:n_mid])
            
            # FWD
            lineup.extend(by_position[4][:n_fwd])
            
            # Calculate total
            total = sum(p[2] for p in lineup)
            
            if total > best_total:
                best_total = total
                best_lineup = lineup
                best_formation = formation
        
        if best_lineup is None:
            raise ValueError("Could not find valid formation")
        
        # Get starting XI player IDs
        starting_ids = {p[0] for p in best_lineup}
        
        # Create PlayerSlot objects for starters
        starting_xi = [
            PlayerSlot(
                player_id=p[0],
                name=p[1],
                position=player_positions.get(p[0], 3),
                predicted_points=p[3],  # Original prediction
                is_starter=True
            )
            for p in best_lineup
        ]
        
        # Sort by position for display
        starting_xi.sort(key=lambda x: (x.position, -x.predicted_points))
        
        # Get bench players (not in starting XI)
        bench_players = [
            (pid, name, pred)
            for pid, name, pred in squad_predictions
            if pid not in starting_ids
        ]
        
        # Sort bench by predicted points (best first for auto-sub)
        bench_players.sort(key=lambda x: x[2], reverse=True)
        
        # Create bench slots
        bench = [
            PlayerSlot(
                player_id=p[0],
                name=p[1],
                position=player_positions.get(p[0], 3),
                predicted_points=p[2],
                is_starter=False,
                bench_order=i
            )
            for i, p in enumerate(bench_players)
        ]
        
        # Format formation string
        formation_str = f"{best_formation[0]}-{best_formation[1]}-{best_formation[2]}"
        
        # Build reasoning
        reasoning = f"Formation: {formation_str}. "
        reasoning += f"Total predicted: {best_total:.1f} points. "
        
        # Note any benched high scorers
        if bench and bench[0].predicted_points > min(s.predicted_points for s in starting_xi):
            reasoning += f"Note: {bench[0].name} benched due to formation constraints. "
        
        return OptimizedLineup(
            starting_xi=starting_xi,
            bench=bench,
            formation=formation_str,
            total_predicted_points=best_total,
            reasoning=reasoning
        )
    
    def get_auto_sub_order(
        self,
        bench: List[PlayerSlot],
        starting_xi: List[PlayerSlot]
    ) -> List[int]:
        """
        Get optimal auto-sub order for bench.
        
        Returns bench player IDs in order they should sub in.
        """
        # Count positions in starting XI
        position_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        for p in starting_xi:
            position_counts[p.position] += 1
        
        # Sort bench by:
        # 1. Can fit into formation (maintain min requirements)
        # 2. Predicted points
        
        def sub_priority(player: PlayerSlot) -> Tuple[int, float]:
            pos = player.position
            
            # GK first if only 1 GK
            if pos == 1:
                return (0, player.predicted_points)
            
            # Check if this position can sub in
            # (formation allows it)
            can_sub = True
            if pos == 2 and position_counts[2] >= 5:
                can_sub = False
            elif pos == 3 and position_counts[3] >= 5:
                can_sub = False
            elif pos == 4 and position_counts[4] >= 3:
                can_sub = False
            
            priority = 1 if can_sub else 2
            return (priority, -player.predicted_points)
        
        sorted_bench = sorted(bench, key=sub_priority)
        return [p.player_id for p in sorted_bench]


