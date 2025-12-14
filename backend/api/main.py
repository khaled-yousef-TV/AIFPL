"""
FPL Squad Suggester API

Suggests optimal squad for the next gameweek using predictions.
No login required - uses public FPL data.
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import our modules
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpl.client import FPLClient
from ml.features import FeatureEngineer
from ml.predictor import HeuristicPredictor
from data.european_teams import assess_rotation_risk, get_european_competition

# Initialize components (no auth needed for public data)
fpl_client = FPLClient(auth=None)
predictor = HeuristicPredictor()


# Create FastAPI app
app = FastAPI(
    title="FPL Squad Suggester",
    description="AI-powered squad suggestions for Fantasy Premier League",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Health Check ====================

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ==================== Gameweek Info ====================

@app.get("/api/gameweek")
async def get_gameweek():
    """Get current and next gameweek info."""
    try:
        current = fpl_client.get_current_gameweek()
        next_gw = fpl_client.get_next_gameweek()
        
        return {
            "current": {
                "id": current.id if current else None,
                "name": current.name if current else None,
                "finished": current.finished if current else None
            } if current else None,
            "next": {
                "id": next_gw.id if next_gw else None,
                "name": next_gw.name if next_gw else None,
                "deadline": next_gw.deadline_time.isoformat() if next_gw and next_gw.deadline_time else None
            } if next_gw else None
        }
    except Exception as e:
        logger.error(f"Error getting gameweek: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Predictions ====================

@app.get("/api/predictions")
async def get_predictions(position: Optional[int] = None, top_n: int = 100):
    """Get player predictions for next gameweek."""
    try:
        players = fpl_client.get_players()
        teams = fpl_client.get_teams()
        team_names = {t.id: t.short_name for t in teams}
        
        next_gw = fpl_client.get_next_gameweek()
        fixtures = fpl_client.get_fixtures(gameweek=next_gw.id if next_gw else None)
        gw_deadline = next_gw.deadline_time if next_gw else datetime.now()
        
        # Build fixture info
        fixture_info = {}
        for f in fixtures:
            fixture_info[f.team_h] = {
                "opponent": team_names.get(f.team_a, "???"),
                "difficulty": f.team_h_difficulty,
                "is_home": True,
            }
            fixture_info[f.team_a] = {
                "opponent": team_names.get(f.team_h, "???"),
                "difficulty": f.team_a_difficulty,
                "is_home": False,
            }
        
        feature_eng = FeatureEngineer(fpl_client)
        
        predictions = []
        for player in players:
            if position and player.element_type != position:
                continue
            if player.minutes < 90:
                continue
            
            try:
                features = feature_eng.extract_features(player.id, include_history=False)
                pred = predictor.predict_player(features)
                
                fix = fixture_info.get(player.team, {})
                opponent = fix.get("opponent", "???")
                difficulty = fix.get("difficulty", 3)
                is_home = fix.get("is_home", False)
                
                team_name = team_names.get(player.team, "???")
                rotation = assess_rotation_risk(team_name, gw_deadline, difficulty)
                
                reasons = []
                if rotation.risk_level in ["high", "medium"]:
                    reasons.append(f"⚠️ {rotation.competition} rotation risk")
                if float(player.form) >= 5.0:
                    reasons.append(f"Form: {player.form}")
                if difficulty <= 2:
                    reasons.append(f"Easy fixture (FDR {difficulty})")
                if is_home:
                    reasons.append("Home advantage")
                if not reasons:
                    reasons.append(f"vs {opponent}")
                
                predictions.append({
                    "id": player.id,
                    "name": player.web_name,
                    "full_name": player.full_name,
                    "team": team_name,
                    "team_id": player.team,
                    "position": player.position,
                    "position_id": player.element_type,
                    "price": player.price,
                    "predicted_points": round(pred, 2),
                    "form": float(player.form),
                    "total_points": player.total_points,
                    "ownership": float(player.selected_by_percent),
                    "opponent": opponent,
                    "difficulty": difficulty,
                    "is_home": is_home,
                    "rotation_risk": rotation.risk_level,
                    "european_comp": rotation.competition,
                    "reason": " • ".join(reasons[:2]),
                    "status": player.status,
                    "news": player.news,
                })
            except Exception as e:
                continue
        
        predictions.sort(key=lambda x: x["predicted_points"], reverse=True)
        return {"predictions": predictions[:top_n]}
        
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Suggested Squad ====================

@app.get("/api/suggested-squad")
async def get_suggested_squad(budget: float = 100.0):
    """Get optimal suggested squad for next gameweek."""
    try:
        players = fpl_client.get_players()
        teams = fpl_client.get_teams()
        team_names = {t.id: t.short_name for t in teams}
        
        next_gw = fpl_client.get_next_gameweek()
        fixtures = fpl_client.get_fixtures(gameweek=next_gw.id if next_gw else None)
        gw_deadline = next_gw.deadline_time if next_gw else datetime.now()
        
        fixture_info = {}
        for f in fixtures:
            fixture_info[f.team_h] = {
                "opponent": team_names.get(f.team_a, "???"),
                "difficulty": f.team_h_difficulty,
                "is_home": True,
            }
            fixture_info[f.team_a] = {
                "opponent": team_names.get(f.team_h, "???"),
                "difficulty": f.team_a_difficulty,
                "is_home": False,
            }
        
        feature_eng = FeatureEngineer(fpl_client)
        
        player_predictions = []
        for player in players:
            if player.minutes < 90 or player.status not in ["a", "d"]:
                continue
            
            try:
                features = feature_eng.extract_features(player.id, include_history=False)
                pred = predictor.predict_player(features)
                
                fix = fixture_info.get(player.team, {})
                opponent = fix.get("opponent", "???")
                difficulty = fix.get("difficulty", 3)
                is_home = fix.get("is_home", False)
                
                team_name = team_names.get(player.team, "???")
                rotation = assess_rotation_risk(team_name, gw_deadline, difficulty)
                
                reasons = []
                if rotation.risk_level == "high":
                    reasons.append(f"⚠️ HIGH rotation ({rotation.competition})")
                elif rotation.risk_level == "medium":
                    reasons.append(f"⚡ Rotation risk ({rotation.competition})")
                
                if float(player.form) >= 5.0:
                    reasons.append(f"Hot form ({player.form})")
                if difficulty <= 2:
                    reasons.append(f"Easy fixture vs {opponent} (FDR {difficulty})")
                elif is_home and difficulty <= 3:
                    reasons.append(f"Home vs {opponent}")
                if float(player.selected_by_percent) < 10 and pred >= 5:
                    reasons.append(f"Differential ({player.selected_by_percent}% owned)")
                if player.total_points >= 70:
                    reasons.append(f"Season performer ({player.total_points} pts)")
                
                if not reasons:
                    reasons.append(f"vs {opponent} ({'H' if is_home else 'A'})")
                
                player_predictions.append({
                    "id": player.id,
                    "name": player.web_name,
                    "team": team_name,
                    "team_id": player.team,
                    "position": player.position,
                    "position_id": player.element_type,
                    "price": player.price,
                    "predicted": pred,
                    "form": float(player.form),
                    "total_points": player.total_points,
                    "ownership": float(player.selected_by_percent),
                    "opponent": opponent,
                    "difficulty": difficulty,
                    "is_home": is_home,
                    "rotation_risk": rotation.risk_level,
                    "european_comp": rotation.competition,
                    "rotation_factor": rotation.risk_factor,
                    "reason": " • ".join(reasons[:2]),
                })
            except:
                continue
        
        squad = _build_optimal_squad(player_predictions, budget)
        starting_xi, bench, formation = _optimize_lineup(squad)
        
        captain = max(starting_xi, key=lambda x: x["predicted"])
        vice_captain = sorted(starting_xi, key=lambda x: x["predicted"], reverse=True)[1]
        
        total_cost = sum(p["price"] for p in squad)
        total_predicted = sum(p["predicted"] for p in starting_xi) + captain["predicted"]
        
        return {
            "gameweek": next_gw.id if next_gw else None,
            "formation": formation,
            "starting_xi": [
                {**p, "is_captain": p["id"] == captain["id"], "is_vice_captain": p["id"] == vice_captain["id"]}
                for p in starting_xi
            ],
            "bench": bench,
            "captain": {"id": captain["id"], "name": captain["name"], "predicted": round(captain["predicted"], 2)},
            "vice_captain": {"id": vice_captain["id"], "name": vice_captain["name"], "predicted": round(vice_captain["predicted"], 2)},
            "total_cost": round(total_cost, 1),
            "remaining_budget": round(budget - total_cost, 1),
            "predicted_points": round(total_predicted, 1),
        }
        
    except Exception as e:
        logger.error(f"Squad suggestion error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _build_optimal_squad(players: List[Dict], budget: float) -> List[Dict]:
    """Build optimal 15-player squad within budget."""
    by_position = {1: [], 2: [], 3: [], 4: []}
    for p in players:
        by_position[p["position_id"]].append(p)
    
    def player_score(p):
        pred = p["predicted"]
        difficulty = p.get("difficulty", 3)
        form = p.get("form", 2)
        price = max(p["price"], 4.0)
        rotation_risk = p.get("rotation_risk", "none")
        rotation_factor = p.get("rotation_factor", 0)
        
        fixture_bonus = (4 - difficulty) * 1.5
        if difficulty == 2:
            fixture_bonus += 1.5
        
        form_bonus = max(0, (form - 3) * 0.3)
        home_bonus = 0.5 if p.get("is_home") else 0
        
        rotation_penalty = 0
        if rotation_risk == "high":
            rotation_penalty = -3.0
        elif rotation_risk == "medium":
            rotation_penalty = -1.5
        elif rotation_risk == "low":
            rotation_penalty = -0.5
        
        if rotation_factor > 0.3 and difficulty <= 2:
            rotation_penalty *= 1.5
        
        score = pred + fixture_bonus + form_bonus + home_bonus + rotation_penalty
        value_factor = pred / price
        
        return score + value_factor * 0.3
    
    for pos in by_position:
        by_position[pos].sort(key=player_score, reverse=True)
    
    squad = []
    team_counts = {}
    remaining_budget = budget
    
    requirements = {1: 2, 2: 5, 3: 5, 4: 3}
    
    for pos_id, count in requirements.items():
        selected = 0
        for player in by_position[pos_id]:
            if selected >= count:
                break
            if player["price"] > remaining_budget:
                continue
            team_id = player["team_id"]
            if team_counts.get(team_id, 0) >= 3:
                continue
            
            squad.append(player)
            remaining_budget -= player["price"]
            team_counts[team_id] = team_counts.get(team_id, 0) + 1
            selected += 1
    
    return squad


def _optimize_lineup(squad: List[Dict]) -> tuple:
    """Optimize starting XI from 15-player squad."""
    by_pos = {1: [], 2: [], 3: [], 4: []}
    for p in squad:
        by_pos[p["position_id"]].append(p)
    
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x["predicted"], reverse=True)
    
    formations = [(3, 5, 2), (3, 4, 3), (4, 5, 1), (4, 4, 2), (4, 3, 3), (5, 4, 1), (5, 3, 2)]
    
    best_xi = None
    best_total = -1
    best_formation = ""
    
    for n_def, n_mid, n_fwd in formations:
        if len(by_pos[2]) < n_def or len(by_pos[3]) < n_mid or len(by_pos[4]) < n_fwd:
            continue
        
        xi = [by_pos[1][0]]
        xi.extend(by_pos[2][:n_def])
        xi.extend(by_pos[3][:n_mid])
        xi.extend(by_pos[4][:n_fwd])
        
        total = sum(p["predicted"] for p in xi)
        if total > best_total:
            best_total = total
            best_xi = xi
            best_formation = f"{n_def}-{n_mid}-{n_fwd}"
    
    xi_ids = {p["id"] for p in best_xi}
    bench = [p for p in squad if p["id"] not in xi_ids]
    bench.sort(key=lambda x: x["predicted"], reverse=True)
    
    best_xi.sort(key=lambda x: (x["position_id"], -x["predicted"]))
    
    for p in best_xi:
        p["predicted"] = round(p["predicted"], 2)
    for p in bench:
        p["predicted"] = round(p["predicted"], 2)
    
    return best_xi, bench, best_formation


@app.get("/api/top-picks")
async def get_top_picks():
    """Get top 5 picks for each position."""
    try:
        result = {}
        for pos_id, pos_name in [(1, "goalkeepers"), (2, "defenders"), (3, "midfielders"), (4, "forwards")]:
            preds = await get_predictions(position=pos_id, top_n=5)
            result[pos_name] = preds["predictions"]
        return result
    except Exception as e:
        logger.error(f"Top picks error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/differentials")
async def get_differentials(max_ownership: float = 10.0, top_n: int = 10):
    """Get differential picks (low ownership, high predicted points)."""
    try:
        preds = await get_predictions(top_n=500)
        differentials = [
            p for p in preds["predictions"]
            if p["ownership"] < max_ownership and p["predicted_points"] >= 4.0
        ]
        differentials.sort(key=lambda x: x["predicted_points"], reverse=True)
        return {"differentials": differentials[:top_n]}
    except Exception as e:
        logger.error(f"Differentials error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
