"""
Wildcard Optimizer with Hybrid LSTM-XGBoost Model

This module provides an 8-gameweek trajectory optimizer for FPL wildcard planning.
Uses a hybrid model combining LSTM and XGBoost predictions with:
- Weighted formula: 0.7×LSTM + 0.3×XGBoost
- Fixture Difficulty Rating (FDR) adjustment
- Transfer decay factor for increasing uncertainty over time
- MILP optimizer for optimal squad path
"""

import logging
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# Try to import optimization libraries
try:
    from pulp import LpMaximize, LpProblem, LpVariable, lpSum, LpStatus, PULP_CBC_CMD
    PULP_AVAILABLE = True
except ImportError:
    PULP_AVAILABLE = False

logger = logging.getLogger(__name__)

# LSTM weights for prediction (simplified - uses form momentum as proxy)
LSTM_WEIGHT = 0.7
XGBOOST_WEIGHT = 0.3

# Transfer decay factors (1.0 for GW1, decreasing for later GWs)
TRANSFER_DECAY = {
    1: 1.0,
    2: 0.95,
    3: 0.90,
    4: 0.85,
    5: 0.82,
    6: 0.80,
    7: 0.78,
    8: 0.75,
}


@dataclass
class GameweekPrediction:
    """Prediction for a player in a specific gameweek."""
    player_id: int
    player_name: str
    team_id: int
    team_name: str
    position: str  # GK, DEF, MID, FWD
    position_id: int
    price: float
    gameweek: int
    
    # Predictions
    lstm_prediction: float = 0.0
    xgboost_prediction: float = 0.0
    hybrid_prediction: float = 0.0
    
    # Fixture info
    opponent: str = ""
    fdr: int = 3
    is_home: bool = True
    
    # Adjusted prediction after FDR
    fdr_adjusted_prediction: float = 0.0
    
    # Final decayed prediction
    decayed_prediction: float = 0.0


@dataclass
class TrajectoryPlayer:
    """Player with predictions across all gameweeks in horizon."""
    player_id: int
    player_name: str
    team_id: int
    team_name: str
    position: str
    position_id: int
    price: float
    form: float
    total_points: int
    ownership: float
    status: str
    
    # Predictions for each gameweek (keyed by GW number)
    gameweek_predictions: Dict[int, GameweekPrediction] = field(default_factory=dict)
    
    # Aggregate metrics
    total_predicted_points: float = 0.0
    avg_fdr: float = 3.0
    fixture_swing: float = 0.0  # Measures fixture difficulty variance


@dataclass
class WildcardTrajectory:
    """Optimal wildcard trajectory result."""
    squad: List[TrajectoryPlayer]
    starting_xi: List[TrajectoryPlayer]
    bench: List[TrajectoryPlayer]
    captain: TrajectoryPlayer
    vice_captain: TrajectoryPlayer
    formation: str
    
    # Gameweek breakdown
    gameweek_predictions: Dict[int, Dict[str, Any]]
    
    # Summary metrics
    total_predicted_points: float = 0.0
    avg_weekly_points: float = 0.0
    total_cost: float = 0.0
    remaining_budget: float = 0.0
    horizon: int = 8
    
    # Trajectory analysis
    fixture_blocks: List[Dict[str, Any]] = field(default_factory=list)
    rationale: str = ""


class HybridPredictor:
    """
    Hybrid LSTM-XGBoost predictor for multi-gameweek predictions.
    
    Uses form momentum as LSTM proxy and standard XGBoost predictions.
    Formula: 0.7 × LSTM + 0.3 × XGBoost
    """
    
    def __init__(self, fpl_client, feature_engineer, base_predictor):
        self.fpl_client = fpl_client
        self.feature_eng = feature_engineer
        self.base_predictor = base_predictor
    
    def predict_lstm(self, features, player_history: List[Dict]) -> float:
        """
        LSTM-style prediction using form momentum.
        
        Uses recent form trajectory and momentum to predict future points.
        This is a simplified proxy for a true LSTM model.
        """
        # Get recent points history
        if not player_history:
            return features.form if features.form > 0 else 2.0
        
        # Get last 5 gameweeks of points
        recent_points = [h.get("total_points", 0) for h in player_history[-5:]]
        
        if not recent_points:
            return features.form if features.form > 0 else 2.0
        
        # Calculate momentum (trend in recent form)
        if len(recent_points) >= 3:
            # Simple linear regression for momentum
            x = np.arange(len(recent_points))
            slope = np.polyfit(x, recent_points, 1)[0]
            momentum = 1 + (slope * 0.1)  # Scale momentum effect
        else:
            momentum = 1.0
        
        # Weighted average of recent points (more weight on recent)
        weights = np.array([0.1, 0.15, 0.2, 0.25, 0.3])[-len(recent_points):]
        weights = weights / weights.sum()
        weighted_avg = np.average(recent_points, weights=weights)
        
        # Apply momentum
        lstm_prediction = weighted_avg * momentum
        
        # Bound the prediction
        return max(1.0, min(15.0, lstm_prediction))
    
    def predict_xgboost(self, features) -> float:
        """XGBoost-style prediction using the base predictor."""
        return self.base_predictor.predict_player(features)
    
    def predict_hybrid(self, features, player_history: List[Dict]) -> float:
        """
        Hybrid prediction combining LSTM and XGBoost.
        
        Formula: 0.7 × LSTM + 0.3 × XGBoost
        """
        lstm_pred = self.predict_lstm(features, player_history)
        xgb_pred = self.predict_xgboost(features)
        
        hybrid = (LSTM_WEIGHT * lstm_pred) + (XGBOOST_WEIGHT * xgb_pred)
        return max(1.0, min(15.0, hybrid))
    
    def adjust_for_fdr(self, prediction: float, fdr: int, position_id: int) -> float:
        """
        Adjust prediction based on Fixture Difficulty Rating.
        
        FDR 1-2: Easy fixtures, boost prediction
        FDR 3: Neutral
        FDR 4-5: Difficult fixtures, reduce prediction
        """
        # Position-specific FDR sensitivity
        # GK/DEF more affected by difficult fixtures (clean sheet potential)
        # FWD/MID more affected by easy fixtures (attacking returns)
        
        if position_id in [1, 2]:  # GK, DEF
            fdr_multiplier = {
                1: 1.25,  # Very easy - high CS potential
                2: 1.15,
                3: 1.0,
                4: 0.85,
                5: 0.70,  # Very hard - low CS potential
            }.get(fdr, 1.0)
        else:  # MID, FWD
            fdr_multiplier = {
                1: 1.20,  # Very easy - high attacking potential
                2: 1.10,
                3: 1.0,
                4: 0.90,
                5: 0.80,  # Very hard - tough opposition
            }.get(fdr, 1.0)
        
        return prediction * fdr_multiplier
    
    def apply_transfer_decay(self, prediction: float, gw_offset: int) -> float:
        """
        Apply transfer decay factor for increasing uncertainty.
        
        GW1: 1.0 (100% confidence)
        GW8: 0.75 (75% confidence)
        """
        decay = TRANSFER_DECAY.get(gw_offset, 0.75)
        return prediction * decay


class WildcardOptimizer:
    """
    8-Gameweek Wildcard Trajectory Optimizer.
    
    Uses MILP to find the optimal squad path prioritizing:
    - Long-term fixture blocks over single-week peaks
    - Team balance and rotation risk
    - Budget constraints
    """
    
    # FPL squad constraints
    POSITION_LIMITS = {1: 2, 2: 5, 3: 5, 4: 3}  # GK, DEF, MID, FWD
    MAX_PER_TEAM = 3
    BUDGET = 100.0
    
    def __init__(self, fpl_client, feature_engineer, predictor):
        """
        Initialize optimizer.
        
        Args:
            fpl_client: FPL API client
            feature_engineer: Feature engineering module
            predictor: Base predictor (Heuristic/XGBoost)
        """
        self.fpl_client = fpl_client
        self.feature_eng = feature_engineer
        self.base_predictor = predictor
        self.hybrid_predictor = HybridPredictor(fpl_client, feature_engineer, predictor)
    
    def _get_player_history(self, player_id: int) -> List[Dict]:
        """Get player's gameweek history."""
        try:
            details = self.fpl_client.get_player_details(player_id)
            return details.get("history", [])
        except Exception:
            return []
    
    def _build_fixture_map(self, start_gw: int, horizon: int) -> Dict[int, Dict[int, Dict]]:
        """
        Build fixture map for all teams over the horizon.
        
        Returns:
            Dict[team_id -> Dict[gameweek -> {opponent, fdr, is_home}]]
        """
        fixture_map = {}
        teams = self.fpl_client.get_teams()
        team_names = {t.id: t.short_name for t in teams}
        
        for gw_offset in range(horizon):
            gw_num = start_gw + gw_offset
            try:
                fixtures = self.fpl_client.get_fixtures(gameweek=gw_num)
                for f in fixtures:
                    # Home team
                    if f.team_h not in fixture_map:
                        fixture_map[f.team_h] = {}
                    fixture_map[f.team_h][gw_num] = {
                        "opponent": team_names.get(f.team_a, "???"),
                        "fdr": f.team_h_difficulty,
                        "is_home": True
                    }
                    
                    # Away team
                    if f.team_a not in fixture_map:
                        fixture_map[f.team_a] = {}
                    fixture_map[f.team_a][gw_num] = {
                        "opponent": team_names.get(f.team_h, "???"),
                        "fdr": f.team_a_difficulty,
                        "is_home": False
                    }
            except Exception as e:
                logger.warning(f"Could not get fixtures for GW{gw_num}: {e}")
        
        return fixture_map
    
    def _build_trajectory_players(
        self,
        start_gw: int,
        horizon: int = 8
    ) -> List[TrajectoryPlayer]:
        """
        Build trajectory predictions for all eligible players.
        
        Args:
            start_gw: Starting gameweek
            horizon: Number of gameweeks to predict
            
        Returns:
            List of TrajectoryPlayer with multi-GW predictions
        """
        players = self.fpl_client.get_players()
        teams = self.fpl_client.get_teams()
        team_names = {t.id: t.short_name for t in teams}
        
        fixture_map = self._build_fixture_map(start_gw, horizon)
        position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
        
        trajectory_players = []
        
        for player in players:
            # Filter ineligible players
            if player.minutes < 1:
                continue
            if player.status in ["i", "s", "u", "n"]:
                continue
            chance = player.chance_of_playing_next_round
            if chance is not None and chance < 50:
                continue
            
            # Skip injured players
            news_lower = (player.news or "").lower()
            if any(kw in news_lower for kw in ["injured", "injury", "suspended", "unavailable"]):
                continue
            
            try:
                # Get base features
                features = self.feature_eng.extract_features(player.id, include_history=False)
                player_history = self._get_player_history(player.id)
                
                traj_player = TrajectoryPlayer(
                    player_id=player.id,
                    player_name=player.web_name,
                    team_id=player.team,
                    team_name=team_names.get(player.team, "???"),
                    position=position_map.get(player.element_type, "MID"),
                    position_id=player.element_type,
                    price=player.price,
                    form=float(player.form),
                    total_points=player.total_points,
                    ownership=float(player.selected_by_percent),
                    status=player.status,
                    gameweek_predictions={}
                )
                
                total_predicted = 0.0
                fdr_values = []
                
                # Generate predictions for each gameweek
                for gw_offset in range(horizon):
                    gw_num = start_gw + gw_offset
                    gw_offset_1based = gw_offset + 1
                    
                    # Get fixture info
                    team_fixtures = fixture_map.get(player.team, {})
                    fixture = team_fixtures.get(gw_num, {"opponent": "???", "fdr": 3, "is_home": True})
                    
                    # Get predictions
                    lstm_pred = self.hybrid_predictor.predict_lstm(features, player_history)
                    xgb_pred = self.hybrid_predictor.predict_xgboost(features)
                    hybrid_pred = self.hybrid_predictor.predict_hybrid(features, player_history)
                    
                    # Adjust for FDR
                    fdr_adjusted = self.hybrid_predictor.adjust_for_fdr(
                        hybrid_pred, fixture["fdr"], player.element_type
                    )
                    
                    # Apply decay
                    decayed = self.hybrid_predictor.apply_transfer_decay(fdr_adjusted, gw_offset_1based)
                    
                    gw_prediction = GameweekPrediction(
                        player_id=player.id,
                        player_name=player.web_name,
                        team_id=player.team,
                        team_name=team_names.get(player.team, "???"),
                        position=position_map.get(player.element_type, "MID"),
                        position_id=player.element_type,
                        price=player.price,
                        gameweek=gw_num,
                        lstm_prediction=round(lstm_pred, 2),
                        xgboost_prediction=round(xgb_pred, 2),
                        hybrid_prediction=round(hybrid_pred, 2),
                        opponent=fixture["opponent"],
                        fdr=fixture["fdr"],
                        is_home=fixture["is_home"],
                        fdr_adjusted_prediction=round(fdr_adjusted, 2),
                        decayed_prediction=round(decayed, 2)
                    )
                    
                    traj_player.gameweek_predictions[gw_num] = gw_prediction
                    total_predicted += decayed
                    fdr_values.append(fixture["fdr"])
                
                traj_player.total_predicted_points = round(total_predicted, 2)
                traj_player.avg_fdr = round(np.mean(fdr_values), 2) if fdr_values else 3.0
                traj_player.fixture_swing = round(np.std(fdr_values), 2) if len(fdr_values) > 1 else 0.0
                
                trajectory_players.append(traj_player)
                
            except Exception as e:
                logger.debug(f"Skipping player {player.id}: {e}")
                continue
        
        return trajectory_players
    
    def _optimize_squad_milp(
        self,
        players: List[TrajectoryPlayer],
        budget: float = 100.0
    ) -> List[TrajectoryPlayer]:
        """
        Use MILP to find optimal 15-man squad.
        
        Objective: Maximize total predicted points over horizon
        Constraints:
        - 2 GK, 5 DEF, 5 MID, 3 FWD
        - Max 3 players per team
        - Total cost <= budget
        """
        if not PULP_AVAILABLE:
            logger.warning("PuLP not available, using greedy fallback")
            return self._greedy_squad_selection(players, budget)
        
        prob = LpProblem("Wildcard_Trajectory", LpMaximize)
        
        # Binary variable for each player
        player_vars = {p.player_id: LpVariable(f"p_{p.player_id}", cat="Binary") for p in players}
        
        # Objective: Maximize total predicted points
        prob += lpSum(
            player_vars[p.player_id] * p.total_predicted_points 
            for p in players
        )
        
        # Budget constraint
        prob += lpSum(
            player_vars[p.player_id] * p.price 
            for p in players
        ) <= budget
        
        # Position constraints
        for pos_id, count in self.POSITION_LIMITS.items():
            pos_players = [p for p in players if p.position_id == pos_id]
            prob += lpSum(player_vars[p.player_id] for p in pos_players) == count
        
        # Team constraint (max 3 per team)
        team_ids = set(p.team_id for p in players)
        for team_id in team_ids:
            team_players = [p for p in players if p.team_id == team_id]
            prob += lpSum(player_vars[p.player_id] for p in team_players) <= self.MAX_PER_TEAM
        
        # Solve
        prob.solve(PULP_CBC_CMD(msg=0))
        
        if LpStatus[prob.status] != "Optimal":
            logger.warning(f"MILP status: {LpStatus[prob.status]}, using greedy fallback")
            return self._greedy_squad_selection(players, budget)
        
        # Extract selected players
        squad = [p for p in players if player_vars[p.player_id].varValue == 1]
        return squad
    
    def _greedy_squad_selection(
        self,
        players: List[TrajectoryPlayer],
        budget: float
    ) -> List[TrajectoryPlayer]:
        """Greedy fallback when MILP fails."""
        squad = []
        remaining = budget
        team_counts = {}
        
        for pos_id, count in self.POSITION_LIMITS.items():
            pos_players = sorted(
                [p for p in players if p.position_id == pos_id],
                key=lambda x: x.total_predicted_points,
                reverse=True
            )
            
            selected = 0
            for p in pos_players:
                if selected >= count:
                    break
                if p.price > remaining:
                    continue
                if team_counts.get(p.team_id, 0) >= self.MAX_PER_TEAM:
                    continue
                
                squad.append(p)
                remaining -= p.price
                team_counts[p.team_id] = team_counts.get(p.team_id, 0) + 1
                selected += 1
        
        return squad
    
    def _optimize_lineup(
        self,
        squad: List[TrajectoryPlayer],
        gameweek: int
    ) -> Tuple[List[TrajectoryPlayer], List[TrajectoryPlayer], str]:
        """
        Optimize starting XI and bench for a specific gameweek.
        
        Returns:
            Tuple of (starting_xi, bench, formation_string)
        """
        # Sort by predicted points for this gameweek
        def get_gw_prediction(p: TrajectoryPlayer) -> float:
            gw_pred = p.gameweek_predictions.get(gameweek)
            return gw_pred.decayed_prediction if gw_pred else p.total_predicted_points / 8
        
        gks = sorted([p for p in squad if p.position_id == 1], key=get_gw_prediction, reverse=True)
        defs = sorted([p for p in squad if p.position_id == 2], key=get_gw_prediction, reverse=True)
        mids = sorted([p for p in squad if p.position_id == 3], key=get_gw_prediction, reverse=True)
        fwds = sorted([p for p in squad if p.position_id == 4], key=get_gw_prediction, reverse=True)
        
        # Try formations
        formations = [
            (3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2), (4, 5, 1), (5, 3, 2), (5, 4, 1)
        ]
        
        best_xi = None
        best_pts = -1
        best_formation = "4-4-2"
        
        for d, m, f in formations:
            if d > len(defs) or m > len(mids) or f > len(fwds):
                continue
            
            xi = gks[:1] + defs[:d] + mids[:m] + fwds[:f]
            pts = sum(get_gw_prediction(p) for p in xi)
            
            if pts > best_pts:
                best_pts = pts
                best_xi = xi
                best_formation = f"{d}-{m}-{f}"
        
        if best_xi is None:
            best_xi = gks[:1] + defs[:4] + mids[:4] + fwds[:2]
        
        bench = [p for p in squad if p not in best_xi]
        bench.sort(key=lambda x: (x.position_id != 1, -get_gw_prediction(x)))
        
        return best_xi, bench, best_formation
    
    def _identify_fixture_blocks(
        self,
        squad: List[TrajectoryPlayer],
        start_gw: int,
        horizon: int
    ) -> List[Dict[str, Any]]:
        """Identify favorable fixture blocks for teams in squad."""
        blocks = []
        team_ids = set(p.team_id for p in squad)
        
        for team_id in team_ids:
            team_players = [p for p in squad if p.team_id == team_id]
            if not team_players:
                continue
            
            team_name = team_players[0].team_name
            
            # Get FDR sequence
            fdr_sequence = []
            for gw_offset in range(horizon):
                gw_num = start_gw + gw_offset
                for p in team_players:
                    gw_pred = p.gameweek_predictions.get(gw_num)
                    if gw_pred:
                        fdr_sequence.append({
                            "gw": gw_num,
                            "fdr": gw_pred.fdr,
                            "opponent": gw_pred.opponent,
                            "is_home": gw_pred.is_home
                        })
                        break
            
            # Identify green runs (FDR <= 2)
            green_runs = []
            current_run = []
            for fixture in fdr_sequence:
                if fixture["fdr"] <= 2:
                    current_run.append(fixture)
                else:
                    if len(current_run) >= 2:
                        green_runs.append(current_run.copy())
                    current_run = []
            if len(current_run) >= 2:
                green_runs.append(current_run)
            
            if green_runs:
                blocks.append({
                    "team": team_name,
                    "players": [p.player_name for p in team_players],
                    "green_runs": green_runs,
                    "avg_fdr": round(np.mean([f["fdr"] for f in fdr_sequence]), 2)
                })
        
        # Sort by best fixture runs
        blocks.sort(key=lambda x: (-len(x["green_runs"]), x["avg_fdr"]))
        return blocks
    
    def _generate_rationale(
        self,
        squad: List[TrajectoryPlayer],
        fixture_blocks: List[Dict],
        horizon: int
    ) -> str:
        """Generate human-readable rationale for the trajectory."""
        lines = []
        
        # Top picks
        top_5 = sorted(squad, key=lambda x: x.total_predicted_points, reverse=True)[:5]
        lines.append("**Top Value Picks:**")
        for p in top_5:
            lines.append(f"• {p.player_name} ({p.team_name}) - {p.total_predicted_points:.1f} pts over {horizon} GWs")
        
        # Fixture analysis
        if fixture_blocks:
            lines.append("\n**Favorable Fixture Blocks:**")
            for block in fixture_blocks[:3]:
                runs_str = ", ".join([
                    f"GW{run[0]['gw']}-{run[-1]['gw']}" 
                    for run in block["green_runs"]
                ])
                lines.append(f"• {block['team']} ({', '.join(block['players'])}) - Green runs: {runs_str}")
        
        # Low ownership differentials
        differentials = [p for p in squad if p.ownership < 10]
        if differentials:
            lines.append("\n**Differentials (<10% owned):**")
            for p in sorted(differentials, key=lambda x: x.total_predicted_points, reverse=True)[:3]:
                lines.append(f"• {p.player_name} - {p.ownership}% owned, {p.total_predicted_points:.1f} pts predicted")
        
        return "\n".join(lines)
    
    def get_optimal_trajectory(
        self,
        budget: float = 100.0,
        horizon: int = 8,
        current_squad: Optional[List[Dict]] = None
    ) -> Optional[WildcardTrajectory]:
        """
        Get optimal 8-GW squad trajectory.
        
        Args:
            budget: Budget constraint
            horizon: Number of gameweeks to optimize for
            current_squad: Current squad (optional, for comparison)
            
        Returns:
            WildcardTrajectory with optimal squad and analysis
        """
        try:
            # Get next gameweek
            next_gw = self.fpl_client.get_next_gameweek()
            if not next_gw:
                logger.error("No next gameweek found")
                return None
            
            start_gw = next_gw.id
            
            logger.info(f"Building trajectory predictions from GW{start_gw} for {horizon} gameweeks")
            
            # Build trajectory players
            trajectory_players = self._build_trajectory_players(start_gw, horizon)
            
            if not trajectory_players:
                logger.error("No eligible players found")
                return None
            
            logger.info(f"Built predictions for {len(trajectory_players)} players")
            
            # Optimize squad using MILP
            squad = self._optimize_squad_milp(trajectory_players, budget)
            
            if len(squad) != 15:
                logger.warning(f"Squad has {len(squad)} players, expected 15")
            
            # Optimize lineup for first gameweek
            starting_xi, bench, formation = self._optimize_lineup(squad, start_gw)
            
            # Select captain and vice captain
            captain = max(starting_xi, key=lambda p: p.total_predicted_points)
            remaining = [p for p in starting_xi if p != captain]
            vice_captain = max(remaining, key=lambda p: p.total_predicted_points)
            
            # Build gameweek breakdown
            gw_predictions = {}
            for gw_offset in range(horizon):
                gw_num = start_gw + gw_offset
                xi, _, gw_formation = self._optimize_lineup(squad, gw_num)
                
                gw_predictions[gw_num] = {
                    "gameweek": gw_num,
                    "formation": gw_formation,
                    "predicted_points": sum(
                        p.gameweek_predictions.get(gw_num, GameweekPrediction(
                            player_id=0, player_name="", team_id=0, team_name="",
                            position="", position_id=0, price=0, gameweek=gw_num
                        )).decayed_prediction
                        for p in xi
                    ),
                    "starting_xi": [
                        {
                            "id": p.player_id,
                            "name": p.player_name,
                            "team": p.team_name,
                            "position": p.position,
                            "predicted": p.gameweek_predictions.get(gw_num).decayed_prediction if p.gameweek_predictions.get(gw_num) else 0,
                            "opponent": p.gameweek_predictions.get(gw_num).opponent if p.gameweek_predictions.get(gw_num) else "???",
                            "fdr": p.gameweek_predictions.get(gw_num).fdr if p.gameweek_predictions.get(gw_num) else 3,
                            "is_home": p.gameweek_predictions.get(gw_num).is_home if p.gameweek_predictions.get(gw_num) else True,
                        }
                        for p in xi
                    ]
                }
            
            # Calculate metrics
            total_cost = sum(p.price for p in squad)
            total_predicted = sum(p.total_predicted_points for p in squad) + captain.total_predicted_points
            
            # Identify fixture blocks
            fixture_blocks = self._identify_fixture_blocks(squad, start_gw, horizon)
            
            # Generate rationale
            rationale = self._generate_rationale(squad, fixture_blocks, horizon)
            
            return WildcardTrajectory(
                squad=squad,
                starting_xi=starting_xi,
                bench=bench,
                captain=captain,
                vice_captain=vice_captain,
                formation=formation,
                gameweek_predictions=gw_predictions,
                total_predicted_points=round(total_predicted, 1),
                avg_weekly_points=round(total_predicted / horizon, 1),
                total_cost=round(total_cost, 1),
                remaining_budget=round(budget - total_cost, 1),
                horizon=horizon,
                fixture_blocks=fixture_blocks,
                rationale=rationale
            )
            
        except Exception as e:
            logger.error(f"Error in trajectory optimization: {e}", exc_info=True)
            return None
    
    def trajectory_to_dict(self, trajectory: WildcardTrajectory) -> Dict[str, Any]:
        """Convert WildcardTrajectory to JSON-serializable dict."""
        
        def player_to_dict(p: TrajectoryPlayer) -> Dict:
            return {
                "id": p.player_id,
                "name": p.player_name,
                "team": p.team_name,
                "team_id": p.team_id,
                "position": p.position,
                "position_id": p.position_id,
                "price": p.price,
                "form": p.form,
                "total_points": p.total_points,
                "ownership": p.ownership,
                "predicted_points": p.total_predicted_points,
                "avg_fdr": p.avg_fdr,
                "fixture_swing": p.fixture_swing,
                "gameweek_predictions": {
                    gw: {
                        "predicted": pred.decayed_prediction,
                        "hybrid": pred.hybrid_prediction,
                        "fdr_adjusted": pred.fdr_adjusted_prediction,
                        "opponent": pred.opponent,
                        "fdr": pred.fdr,
                        "is_home": pred.is_home,
                    }
                    for gw, pred in p.gameweek_predictions.items()
                }
            }
        
        return {
            "squad": [player_to_dict(p) for p in trajectory.squad],
            "starting_xi": [player_to_dict(p) for p in trajectory.starting_xi],
            "bench": [player_to_dict(p) for p in trajectory.bench],
            "captain": player_to_dict(trajectory.captain),
            "vice_captain": player_to_dict(trajectory.vice_captain),
            "formation": trajectory.formation,
            "gameweek_predictions": trajectory.gameweek_predictions,
            "total_predicted_points": trajectory.total_predicted_points,
            "avg_weekly_points": trajectory.avg_weekly_points,
            "total_cost": trajectory.total_cost,
            "remaining_budget": trajectory.remaining_budget,
            "horizon": trajectory.horizon,
            "fixture_blocks": trajectory.fixture_blocks,
            "rationale": trajectory.rationale,
        }

