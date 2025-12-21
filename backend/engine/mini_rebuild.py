"""
Mini Rebuild Engine

Coordinated multi-transfer optimization that considers all transfers together
as a cohesive unit, enforcing formation constraints and optimizing for total points gain.
"""

import logging
from typing import List, Dict, Tuple, Optional, Set
from itertools import combinations
from dataclasses import dataclass

from backend.constants import (
    PlayerPosition, MAX_GK, MAX_DEF, MAX_MID, MAX_FWD,
    MAX_PLAYERS_PER_TEAM, SQUAD_SIZE
)

logger = logging.getLogger(__name__)


@dataclass
class MiniRebuildPlan:
    """Coordinated transfer plan for mini rebuild."""
    transfers_out: List[Dict]
    transfers_in: List[Dict]
    total_points_gain: float
    total_cost: float
    resulting_squad: Dict
    combined_rationale: str
    individual_breakdowns: List[Dict]


class MiniRebuildEngine:
    """
    Engine for coordinated multi-transfer optimization.
    
    Considers:
    - All transfers together as a unit
    - Formation constraints (strict 2-5-5-3)
    - Total budget across all transfers
    - Team balance (max 3 per team)
    - Combined fixture runs
    - Total points optimization
    """
    
    def __init__(self):
        """Initialize mini rebuild engine."""
        pass
    
    def generate_plan(
        self,
        current_squad: List[Dict],
        all_players: List[Dict],
        bank: float,
        free_transfers: int,
        player_predictions: Dict[int, float],
        fixture_info: Dict[int, Dict],
        avg_fixture_5gw: Dict[int, float],
        team_counts: Dict[int, int],
        team_names: Dict[int, str]
    ) -> Optional[MiniRebuildPlan]:
        """
        Generate coordinated transfer plan.
        
        Args:
            current_squad: Current squad players with id, position, price, team_id
            all_players: All available players with predictions and scores
            bank: Available budget
            free_transfers: Number of free transfers (must be >= 4)
            player_predictions: Dict of player_id -> predicted points
            fixture_info: Dict of team_id -> fixture info
            avg_fixture_5gw: Dict of team_id -> avg fixture difficulty
            team_counts: Current count of players per team
            team_names: Dict of team_id -> team name
            
        Returns:
            MiniRebuildPlan or None if no valid plan found
        """
        if free_transfers < 4:
            logger.warning(f"Mini rebuild requires 4+ transfers, got {free_transfers}")
            return None
        
        # Analyze current squad
        squad_by_pos = self._group_by_position(current_squad)
        
        # Validate current formation
        current_formation = {
            "GK": len(squad_by_pos.get(PlayerPosition.GK, [])),
            "DEF": len(squad_by_pos.get(PlayerPosition.DEF, [])),
            "MID": len(squad_by_pos.get(PlayerPosition.MID, [])),
            "FWD": len(squad_by_pos.get(PlayerPosition.FWD, []))
        }
        
        # Find worst players to transfer out (prioritize by keep_score or predicted)
        transfer_out_candidates = self._find_worst_players(
            current_squad, player_predictions, fixture_info, avg_fixture_5gw, free_transfers
        )
        
        if len(transfer_out_candidates) < free_transfers:
            logger.warning(f"Not enough transfer-out candidates: {len(transfer_out_candidates)} < {free_transfers}")
            return None
        
        # Select N worst players (where N = free_transfers)
        selected_outs = transfer_out_candidates[:free_transfers]
        
        # Calculate position distribution after removing selected players
        out_positions = {}
        for player in selected_outs:
            pos = player.get("position_id") or self._get_position_id(player.get("position", ""))
            out_positions[pos] = out_positions.get(pos, 0) + 1
        
        # Calculate resulting formation after removing outs
        resulting_formation = {
            PlayerPosition.GK: current_formation["GK"] - out_positions.get(PlayerPosition.GK, 0),
            PlayerPosition.DEF: current_formation["DEF"] - out_positions.get(PlayerPosition.DEF, 0),
            PlayerPosition.MID: current_formation["MID"] - out_positions.get(PlayerPosition.MID, 0),
            PlayerPosition.FWD: current_formation["FWD"] - out_positions.get(PlayerPosition.FWD, 0),
        }
        
        # Calculate total budget after selling
        total_selling_price = sum(p.get("price", 0) for p in selected_outs)
        total_budget = bank + total_selling_price
        
        # Calculate team counts after removing outs
        new_team_counts = dict(team_counts)
        for player in selected_outs:
            team_id = player.get("team_id")
            if team_id:
                new_team_counts[team_id] = max(0, new_team_counts.get(team_id, 0) - 1)
        
        # Find best replacements that maintain formation constraints
        # Match replacements to outs by position to maintain formation
        replacements = []
        used_in_ids = set()
        
        # Group outs by position to match replacements
        for out_player in selected_outs:
            pos_id = out_player.get("position_id") or self._get_position_id(out_player.get("position", ""))
            max_price = out_player.get("price", 0) + total_budget
            
            # Find best replacement for this specific out player
            candidates = []
            for player in all_players:
                if player.get("id") in used_in_ids:
                    continue
                
                player_pos = player.get("position_id") or self._get_position_id(player.get("position", ""))
                if player_pos != pos_id:
                    continue
                
                if player.get("price", 0) > max_price:
                    continue
                
                if player.get("status") in ["i", "s", "u", "n"]:
                    continue
                
                player_team = player.get("team_id")
                if player_team and new_team_counts.get(player_team, 0) >= MAX_PLAYERS_PER_TEAM:
                    continue
                
                # Calculate buy score
                pred = player_predictions.get(player.get("id"), 0)
                buy_score = pred
                
                if player_team:
                    fix = fixture_info.get(player_team, {})
                    if fix.get("difficulty", 3) <= 2:
                        buy_score += 2.0
                    
                    avg_diff = avg_fixture_5gw.get(player_team, 3.0)
                    if avg_diff <= 2.5:
                        buy_score += 1.5
                
                form = player.get("form", 0)
                if form >= 6.0:
                    buy_score += 1.5
                elif form >= 4.0:
                    buy_score += 0.5
                
                candidates.append({
                    **player,
                    "predicted": pred,
                    "buy_score": buy_score
                })
            
            candidates.sort(key=lambda x: x.get("buy_score", 0), reverse=True)
            
            if candidates:
                best = candidates[0]
                replacements.append(best)
                used_in_ids.add(best.get("id"))
                total_budget -= (best.get("price", 0) - out_player.get("price", 0))
                
                # Update team counts
                best_team = best.get("team_id")
                if best_team:
                    new_team_counts[best_team] = new_team_counts.get(best_team, 0) + 1
        
        if len(replacements) < free_transfers:
            logger.warning(f"Not enough valid replacements found: {len(replacements)} < {free_transfers}")
            return None
        
        # Calculate total points gain and cost
        total_points_gain = sum(
            replacements[i].get("predicted", 0) - selected_outs[i].get("predicted", 0)
            for i in range(len(replacements))
        )
        
        total_cost = sum(
            replacements[i].get("price", 0) - selected_outs[i].get("price", 0)
            for i in range(len(replacements))
        )
        
        # Build resulting squad
        resulting_squad = self._build_resulting_squad(
            current_squad, selected_outs, replacements
        )
        
        # Generate combined rationale
        combined_rationale = self._generate_combined_rationale(
            selected_outs, replacements, total_points_gain, total_cost
        )
        
        # Build individual breakdowns
        individual_breakdowns = [
            {
                "out": {
                    "id": selected_outs[i].get("id"),
                    "name": selected_outs[i].get("name"),
                    "team": selected_outs[i].get("team"),
                    "position": selected_outs[i].get("position"),
                    "price": selected_outs[i].get("price"),
                    "predicted": selected_outs[i].get("predicted", 0),
                },
                "in": {
                    "id": replacements[i].get("id"),
                    "name": replacements[i].get("name"),
                    "team": replacements[i].get("team"),
                    "position": replacements[i].get("position"),
                    "price": replacements[i].get("price"),
                    "predicted": replacements[i].get("predicted", 0),
                },
                "points_gain": round(replacements[i].get("predicted", 0) - selected_outs[i].get("predicted", 0), 2),
                "cost": round(replacements[i].get("price", 0) - selected_outs[i].get("price", 0), 1),
                "reason": self._generate_individual_reason(selected_outs[i], replacements[i])
            }
            for i in range(len(replacements))
        ]
        
        return MiniRebuildPlan(
            transfers_out=selected_outs,
            transfers_in=replacements,
            total_points_gain=round(total_points_gain, 2),
            total_cost=round(total_cost, 1),
            resulting_squad=resulting_squad,
            combined_rationale=combined_rationale,
            individual_breakdowns=individual_breakdowns
        )
    
    def _group_by_position(self, squad: List[Dict]) -> Dict[int, List[Dict]]:
        """Group squad by position ID."""
        grouped = {PlayerPosition.GK: [], PlayerPosition.DEF: [], 
                   PlayerPosition.MID: [], PlayerPosition.FWD: []}
        for player in squad:
            pos_id = player.get("position_id") or self._get_position_id(player.get("position", ""))
            if pos_id in grouped:
                grouped[pos_id].append(player)
        return grouped
    
    def _get_position_id(self, position: str) -> int:
        """Convert position string to ID."""
        pos_map = {"GK": PlayerPosition.GK, "DEF": PlayerPosition.DEF,
                   "MID": PlayerPosition.MID, "FWD": PlayerPosition.FWD}
        return pos_map.get(position.upper(), PlayerPosition.MID)
    
    def _find_worst_players(
        self,
        squad: List[Dict],
        predictions: Dict[int, float],
        fixture_info: Dict[int, Dict],
        avg_fixture_5gw: Dict[int, float],
        count: int
    ) -> List[Dict]:
        """Find worst players to transfer out."""
        scored_players = []
        for player in squad:
            player_id = player.get("id")
            pred = predictions.get(player_id, 0)
            
            # Calculate keep score (lower = worse)
            keep_score = pred
            
            # Penalize bad fixtures
            team_id = player.get("team_id")
            if team_id:
                fix = fixture_info.get(team_id, {})
                if fix.get("difficulty", 3) >= 4:
                    keep_score -= 1.5
                
                avg_diff = avg_fixture_5gw.get(team_id, 3.0)
                if avg_diff >= 3.5:
                    keep_score -= 1.0
            
            # Penalize injuries
            status = player.get("status", "a")
            if status in ["i", "s", "u", "n"]:
                keep_score -= 5.0
            elif status == "d":
                keep_score -= 1.5
            
            scored_players.append({
                **player,
                "keep_score": keep_score,
                "predicted": pred
            })
        
        # Sort by keep_score (worst first)
        scored_players.sort(key=lambda x: x.get("keep_score", 0))
        return scored_players[:count]
    
    
    def _build_resulting_squad(
        self,
        current_squad: List[Dict],
        outs: List[Dict],
        ins: List[Dict]
    ) -> Dict:
        """Build resulting squad after transfers."""
        out_ids = {p.get("id") for p in outs}
        
        # Create mapping of out -> in by matching position and order
        # This ensures we replace the right players
        resulting = []
        out_index = 0
        
        for player in current_squad:
            if player.get("id") not in out_ids:
                resulting.append(player)
            else:
                # Replace with corresponding new player
                if out_index < len(ins):
                    resulting.append(ins[out_index])
                    out_index += 1
        
        return {
            "squad": resulting,
            "formation": self._calculate_formation(resulting)
        }
    
    def _calculate_formation(self, squad: List[Dict]) -> Dict[str, int]:
        """Calculate formation from squad."""
        formation = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
        for player in squad:
            pos = player.get("position", "")
            if pos in formation:
                formation[pos] += 1
        return formation
    
    def _generate_combined_rationale(
        self,
        outs: List[Dict],
        ins: List[Dict],
        points_gain: float,
        cost: float
    ) -> str:
        """Generate rationale explaining why this combination works."""
        reasons = []
        
        reasons.append(f"Coordinated {len(outs)}-player rebuild optimizing for total points gain (+{points_gain:.1f} points).")
        
        if cost < 0:
            reasons.append(f"Saves £{abs(cost):.1f}m while improving squad quality.")
        elif cost > 0:
            reasons.append(f"Costs £{cost:.1f}m but significantly improves predicted points.")
        else:
            reasons.append("Budget-neutral while improving squad quality.")
        
        # Analyze position improvements
        pos_improvements = {}
        for i in range(len(ins)):
            pos = ins[i].get("position", "")
            gain = ins[i].get("predicted", 0) - outs[i].get("predicted", 0)
            if pos not in pos_improvements:
                pos_improvements[pos] = []
            pos_improvements[pos].append(gain)
        
        if pos_improvements:
            best_pos = max(pos_improvements.items(), key=lambda x: sum(x[1]))
            reasons.append(f"Strongest improvement in {best_pos[0]} position (+{sum(best_pos[1]):.1f} points).")
        
        return " ".join(reasons)
    
    def _generate_individual_reason(self, out: Dict, ins: Dict) -> str:
        """Generate reason for individual transfer."""
        reasons = []
        
        pred_gain = ins.get("predicted", 0) - out.get("predicted", 0)
        if pred_gain > 0:
            reasons.append(f"+{pred_gain:.1f} predicted points")
        
        form_out = out.get("form", 0)
        form_in = ins.get("form", 0)
        if form_in > form_out + 1:
            reasons.append(f"Form upgrade: {form_out} → {form_in}")
        
        fix_out = out.get("fixture_difficulty", 3)
        fix_in = ins.get("fixture_difficulty", 3)
        if fix_in < fix_out:
            reasons.append(f"Better fixture (FDR {fix_out} → {fix_in})")
        
        if not reasons:
            reasons.append("Squad optimization")
        
        return " • ".join(reasons)

