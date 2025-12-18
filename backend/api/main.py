"""
FPL Squad Suggester API

Suggests optimal squad for the next gameweek using predictions.
No login required - uses public FPL data.
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from time import time
from threading import Lock

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
from ml.predictor import HeuristicPredictor, FormPredictor, FixturePredictor
from data.european_teams import assess_rotation_risk, get_european_competition

# Initialize components (no auth needed for public data)
fpl_client = FPLClient(auth=None)
predictor_heuristic = HeuristicPredictor()
predictor_form = FormPredictor()
predictor_fixture = FixturePredictor()
feature_eng = FeatureEngineer(fpl_client)

# Simple in-memory caches to keep the UI snappy (especially in dev with single uvicorn worker)
_CACHE_TTL_SECONDS = int(os.getenv("FPL_CACHE_TTL_SECONDS", "300"))
_cache_lock = Lock()
_cache: Dict[str, Dict[Any, Any]] = {
    "predictions": {},  # key -> (ts, list)
    "squad": {},        # key -> (ts, dict)
}


def _cache_get(namespace: str, key: Any):
    with _cache_lock:
        item = _cache.get(namespace, {}).get(key)
        if not item:
            return None
        ts, data = item
        if time() - ts > _CACHE_TTL_SECONDS:
            _cache[namespace].pop(key, None)
            return None
        return data


def _cache_set(namespace: str, key: Any, data: Any):
    with _cache_lock:
        _cache.setdefault(namespace, {})[key] = (time(), data)


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
        next_gw = fpl_client.get_next_gameweek()
        gw_id = next_gw.id if next_gw else 0

        cache_key = ("heuristic", gw_id)
        all_predictions = _cache_get("predictions", cache_key)

        if all_predictions is None:
            players = fpl_client.get_players()
            teams = fpl_client.get_teams()
            team_names = {t.id: t.short_name for t in teams}

            fixtures = fpl_client.get_fixtures(gameweek=gw_id if gw_id else None)
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

            predictions = []
            total_players = len(players)
            filtered_minutes = 0
            filtered_status = 0
            errors = 0

            for player in players:
                if player.minutes < 1:
                    filtered_minutes += 1
                    continue
                if player.status in ["i", "s", "u"]:
                    filtered_status += 1
                    continue

                try:
                    features = feature_eng.extract_features(player.id, include_history=False)
                    pred = predictor_heuristic.predict_player(features)

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
                    errors += 1
                    if errors <= 5:
                        logger.warning(f"Error predicting {player.web_name}: {e}")
                    continue

            logger.info(
                f"Predictions: {total_players} total, {filtered_minutes} filtered (minutes), "
                f"{filtered_status} filtered (status), {errors} errors, {len(predictions)} successful"
            )

            predictions.sort(key=lambda x: x["predicted_points"], reverse=True)
            all_predictions = predictions
            _cache_set("predictions", cache_key, all_predictions)

        filtered = all_predictions
        if position is not None:
            filtered = [p for p in filtered if p.get("position_id") == position]

        return {"predictions": filtered[:top_n]}
        
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Helper: Build Squad with Predictor ====================

async def _build_squad_with_predictor(
    predictor,
    method_name: str,
    budget: float = 100.0
) -> Dict[str, Any]:
    """Build squad using a specific predictor method."""
    next_gw = fpl_client.get_next_gameweek()
    gw_id = next_gw.id if next_gw else 0
    cache_key = (method_name, gw_id, round(budget, 1))
    cached = _cache_get("squad", cache_key)
    if cached is not None:
        return cached

    players = fpl_client.get_players()
    teams = fpl_client.get_teams()
    team_names = {t.id: t.short_name for t in teams}

    fixtures = fpl_client.get_fixtures(gameweek=gw_id if gw_id else None)
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
    
    player_predictions = []
    for player in players:
        # Allow players with at least 1 minute (includes new signings, rotation players)
        if player.minutes < 1:
            continue
        # Skip unavailable players (injured/suspended) but allow doubtful
        if player.status in ["i", "s", "u"]:
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
    
    result = {
        "method": method_name,
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

    _cache_set("squad", cache_key, result)
    return result


# ==================== Suggested Squad ====================

@app.get("/api/suggested-squad")
async def get_suggested_squad(budget: float = 100.0, method: str = "combined"):
    """
    Get optimal suggested squad for next gameweek.
    
    Args:
        budget: Total budget in millions (default 100.0)
        method: Prediction method - "heuristic", "form", "fixture", or "combined" (default)
    """
    try:
        if method == "heuristic":
            return await _build_squad_with_predictor(predictor_heuristic, "Heuristic (Balanced)", budget)
        elif method == "form":
            return await _build_squad_with_predictor(predictor_form, "Form-Focused", budget)
        elif method == "fixture":
            return await _build_squad_with_predictor(predictor_fixture, "Fixture-Focused", budget)
        else:  # combined
            # Get predictions from all 3 methods
            heuristic_squad = await _build_squad_with_predictor(predictor_heuristic, "Heuristic", budget)
            form_squad = await _build_squad_with_predictor(predictor_form, "Form", budget)
            fixture_squad = await _build_squad_with_predictor(predictor_fixture, "Fixture", budget)
            
            # Average predictions for each player
            all_players = {}
            
            for squad in [heuristic_squad, form_squad, fixture_squad]:
                for player in squad["starting_xi"] + squad["bench"]:
                    pid = player["id"]
                    if pid not in all_players:
                        all_players[pid] = {
                            **player,
                            "predictions": [],
                            "count": 0,
                        }
                    all_players[pid]["predictions"].append(player["predicted"])
                    all_players[pid]["count"] += 1
            
            # Calculate averaged predictions
            averaged_players = []
            for pid, pdata in all_players.items():
                avg_pred = sum(pdata["predictions"]) / len(pdata["predictions"])
                averaged_players.append({
                    **{k: v for k, v in pdata.items() if k not in ["predictions", "count"]},
                    "predicted": round(avg_pred, 2),
                    "method_count": pdata["count"],
                })
            
            # Build combined squad from averaged predictions
            combined_squad = _build_optimal_squad(averaged_players, budget)
            starting_xi, bench, formation = _optimize_lineup(combined_squad)
            
            captain = max(starting_xi, key=lambda x: x["predicted"])
            vice_captain = sorted(starting_xi, key=lambda x: x["predicted"], reverse=True)[1]
            
            total_cost = sum(p["price"] for p in combined_squad)
            total_predicted = sum(p["predicted"] for p in starting_xi) + captain["predicted"]
            
            return {
                "method": "Combined (Averaged)",
                "gameweek": heuristic_squad["gameweek"],
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


# ==================== Transfer Suggestions ====================

class SquadPlayer(BaseModel):
    """Player in user's squad."""
    id: int
    name: str
    position: str  # GK, DEF, MID, FWD
    price: float  # Current selling price


class TransferRequest(BaseModel):
    """Request for transfer suggestions."""
    squad: List[SquadPlayer]
    bank: float = 0.0  # Money in the bank
    free_transfers: int = 1


@app.post("/api/transfer-suggestions")
async def get_transfer_suggestions(request: TransferRequest):
    """
    Get top 3 transfer suggestions based on user's current squad.
    
    Considers:
    - Next GW predicted points
    - Long-term fixture difficulty (next 5 GWs)
    - Player form and value
    - European rotation risk
    - Price trends
    """
    try:
        players = fpl_client.get_players()
        teams = fpl_client.get_teams()
        team_names = {t.id: t.short_name for t in teams}
        
        next_gw = fpl_client.get_next_gameweek()
        fixtures = fpl_client.get_fixtures(gameweek=next_gw.id if next_gw else None)
        gw_deadline = next_gw.deadline_time if next_gw else datetime.now()
        
        # Build fixture info for next GW
        fixture_info = {}
        for f in fixtures:
            fixture_info[f.team_h] = {"opponent": team_names.get(f.team_a, "???"), "difficulty": f.team_h_difficulty, "is_home": True}
            fixture_info[f.team_a] = {"opponent": team_names.get(f.team_h, "???"), "difficulty": f.team_a_difficulty, "is_home": False}
        
        # Get next 5 GW fixtures for long-term analysis
        long_term_fixtures = {}
        for gw_offset in range(5):
            gw_num = (next_gw.id if next_gw else 1) + gw_offset
            try:
                gw_fixtures = fpl_client.get_fixtures(gameweek=gw_num)
                for f in gw_fixtures:
                    if f.team_h not in long_term_fixtures:
                        long_term_fixtures[f.team_h] = []
                    if f.team_a not in long_term_fixtures:
                        long_term_fixtures[f.team_a] = []
                    long_term_fixtures[f.team_h].append(f.team_h_difficulty)
                    long_term_fixtures[f.team_a].append(f.team_a_difficulty)
            except:
                pass
        
        # Calculate average fixture difficulty for next 5 GWs
        avg_fixture_difficulty = {}
        for team_id, diffs in long_term_fixtures.items():
            avg_fixture_difficulty[team_id] = sum(diffs) / len(diffs) if diffs else 3.0
        
        feature_eng = FeatureEngineer(fpl_client)
        
        # Get squad player IDs
        squad_ids = {p.id for p in request.squad}
        squad_by_pos = {"GK": [], "DEF": [], "MID": [], "FWD": []}
        for p in request.squad:
            squad_by_pos[p.position].append(p)
        
        # Analyze each player in squad - find worst performers
        squad_analysis = []
        for squad_player in request.squad:
            player = next((p for p in players if p.id == squad_player.id), None)
            if not player:
                continue
            
            team_name = team_names.get(player.team, "???")
            fix = fixture_info.get(player.team, {})
            rotation = assess_rotation_risk(team_name, gw_deadline, fix.get("difficulty", 3))
            
            try:
                features = feature_eng.extract_features(player.id, include_history=False)
                pred = predictor_heuristic.predict_player(features)
            except:
                pred = float(player.form) if player.form else 2.0
            
            # Calculate "keep score" - lower = more likely to transfer out
            keep_score = pred
            
            # Penalize bad upcoming fixture
            if fix.get("difficulty", 3) >= 4:
                keep_score -= 1.5
            
            # Penalize bad long-term fixtures
            avg_diff = avg_fixture_difficulty.get(player.team, 3.0)
            if avg_diff >= 3.5:
                keep_score -= 1.0
            
            # Penalize rotation risk
            if rotation.risk_level == "high":
                keep_score -= 2.0
            elif rotation.risk_level == "medium":
                keep_score -= 1.0
            
            # Penalize poor form
            if float(player.form) < 3.0:
                keep_score -= 1.0
            
            # Penalize injury doubts
            if player.status == "d":
                keep_score -= 1.5
            elif player.status in ["i", "s", "u"]:
                keep_score -= 5.0
            
            squad_analysis.append({
                "id": player.id,
                "name": player.web_name,
                "team": team_name,
                "position": squad_player.position,
                "price": squad_player.price,
                "predicted": round(pred, 2),
                "form": float(player.form),
                "keep_score": round(keep_score, 2),
                "fixture": fix.get("opponent", "???"),
                "fixture_difficulty": fix.get("difficulty", 3),
                "avg_fixture_5gw": round(avg_diff, 2),
                "rotation_risk": rotation.risk_level,
                "status": player.status,
            })
        
        # Sort by keep_score - worst players first (transfer out candidates)
        squad_analysis.sort(key=lambda x: x["keep_score"])
        transfer_out_candidates = squad_analysis[:5]  # Top 5 worst
        
        # Find best replacements for each position
        transfer_suggestions = []
        
        for out_player in transfer_out_candidates:
            pos = out_player["position"]
            max_price = out_player["price"] + request.bank
            
            # Find best replacements
            replacements = []
            for player in players:
                # Skip if already in squad
                if player.id in squad_ids:
                    continue
                
                # Skip wrong position
                if player.position != pos:
                    continue
                
                # Skip if too expensive
                if player.price > max_price:
                    continue
                
                # Skip unavailable players (injured/suspended) but allow doubtful
                if player.status in ["i", "s", "u"]:
                    continue
                
                # Allow players with at least 1 minute (includes new signings, rotation players)
                if player.minutes < 1:
                    continue
                
                team_name = team_names.get(player.team, "???")
                fix = fixture_info.get(player.team, {})
                rotation = assess_rotation_risk(team_name, gw_deadline, fix.get("difficulty", 3))
                avg_diff = avg_fixture_difficulty.get(player.team, 3.0)
                
                try:
                    features = feature_eng.extract_features(player.id, include_history=False)
                    pred = predictor_heuristic.predict_player(features)
                except:
                    pred = float(player.form) if player.form else 2.0
                
                # Calculate "buy score" - higher = better transfer in
                buy_score = pred
                
                # Bonus for good upcoming fixture
                if fix.get("difficulty", 3) <= 2:
                    buy_score += 2.0
                
                # Bonus for good long-term fixtures
                if avg_diff <= 2.5:
                    buy_score += 1.5
                elif avg_diff <= 3.0:
                    buy_score += 0.5
                
                # Penalize rotation risk
                if rotation.risk_level == "high":
                    buy_score -= 2.0
                elif rotation.risk_level == "medium":
                    buy_score -= 1.0
                
                # Bonus for hot form
                if float(player.form) >= 6.0:
                    buy_score += 1.5
                elif float(player.form) >= 4.0:
                    buy_score += 0.5
                
                # Bonus for differentials
                if float(player.selected_by_percent) < 10:
                    buy_score += 0.5
                
                replacements.append({
                    "id": player.id,
                    "name": player.web_name,
                    "team": team_name,
                    "position": pos,
                    "price": player.price,
                    "predicted": round(pred, 2),
                    "form": float(player.form),
                    "buy_score": round(buy_score, 2),
                    "fixture": fix.get("opponent", "???"),
                    "fixture_difficulty": fix.get("difficulty", 3),
                    "avg_fixture_5gw": round(avg_diff, 2),
                    "rotation_risk": rotation.risk_level,
                    "european_comp": rotation.competition,
                    "ownership": float(player.selected_by_percent),
                })
            
            # Sort by buy_score
            replacements.sort(key=lambda x: x["buy_score"], reverse=True)
            
            if replacements:
                best_replacement = replacements[0]
                points_gain = best_replacement["predicted"] - out_player["predicted"]
                
                # Generate reason
                reasons = []
                if out_player["fixture_difficulty"] >= 4 and best_replacement["fixture_difficulty"] <= 2:
                    reasons.append(f"Fixture swing: {out_player['fixture']} (FDR {out_player['fixture_difficulty']}) → {best_replacement['fixture']} (FDR {best_replacement['fixture_difficulty']})")
                if out_player["avg_fixture_5gw"] > best_replacement["avg_fixture_5gw"] + 0.5:
                    reasons.append(f"Better long-term fixtures ({best_replacement['avg_fixture_5gw']} vs {out_player['avg_fixture_5gw']} avg FDR)")
                if best_replacement["form"] > out_player["form"] + 2:
                    reasons.append(f"Form upgrade: {out_player['form']} → {best_replacement['form']}")
                if out_player["status"] != "a":
                    reasons.append(f"{out_player['name']} is {out_player['status']} (doubtful/injured)")
                if out_player["rotation_risk"] in ["high", "medium"] and best_replacement["rotation_risk"] == "none":
                    reasons.append("Avoids European rotation")
                if not reasons:
                    reasons.append(f"+{round(points_gain, 1)} predicted points")
                
                transfer_suggestions.append({
                    "out": out_player,
                    "in": best_replacement,
                    "cost": round(best_replacement["price"] - out_player["price"], 1),
                    "points_gain": round(points_gain, 2),
                    "priority_score": round(best_replacement["buy_score"] - out_player["keep_score"], 2),
                    "reason": reasons[0],
                    "all_reasons": reasons,
                })
        
        # Sort by priority score and take top 3
        transfer_suggestions.sort(key=lambda x: x["priority_score"], reverse=True)
        top_suggestions = transfer_suggestions[:3]
        
        return {
            "squad_analysis": squad_analysis,
            "suggestions": top_suggestions,
            "bank": request.bank,
            "free_transfers": request.free_transfers,
        }
        
    except Exception as e:
        logger.error(f"Transfer suggestion error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Search Players ====================

@app.get("/api/players/search")
async def search_players(q: str = "", position: Optional[str] = None, limit: int = 50):
    """Search players by name or team for squad input."""
    try:
        players = fpl_client.get_players()
        teams = fpl_client.get_teams()
        team_names = {t.id: t.short_name for t in teams}

        q_lower = (q or "").strip().lower()
        limit = max(1, min(100, int(limit or 50)))

        # Filter by position first
        filtered = players
        if position:
            filtered = [p for p in filtered if p.position == position]

        # If q is empty, return cheapest players for that position (bench fodder)
        if not q_lower:
            filtered.sort(key=lambda p: (p.price, -p.minutes))
            filtered = filtered[: min(20, limit)]
        else:
            # Allow searching by team name/short code too (e.g., "spurs", "tottenham", "TOT")
            team_match_ids = set()
            for t in teams:
                t_name = (t.name or "").lower()
                t_short = (t.short_name or "").lower()
                if q_lower in t_name or q_lower == t_short or q_lower in t_short:
                    team_match_ids.add(t.id)

            # Small alias support (common fan names)
            if q_lower in {"spurs", "tottenham", "tot"}:
                for t in teams:
                    if (t.short_name or "").lower() == "tot" or "spurs" in (t.name or "").lower():
                        team_match_ids.add(t.id)

            ranked = []
            for p in filtered:
                web = p.web_name.lower()
                full = p.full_name.lower()
                name_hit = (q_lower in web) or (q_lower in full)
                team_hit = p.team in team_match_ids
                if not (name_hit or team_hit):
                    continue

                rank = 0
                if web == q_lower or full == q_lower:
                    rank += 3
                if name_hit:
                    rank += 2
                if team_hit:
                    rank += 1

                ranked.append((-rank, -p.minutes, p.price, p.web_name, p))

            ranked.sort()
            filtered = [x[-1] for x in ranked][:limit]

        results = [{
            "id": p.id,
            "name": p.web_name,
            "full_name": p.full_name,
            "team": team_names.get(p.team, "???"),
            "position": p.position,
            "price": p.price,
            "minutes": p.minutes,
            "status": p.status,
        } for p in filtered]

        return {"players": results}
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
