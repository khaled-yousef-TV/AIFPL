"""
FPL Agent API

FastAPI backend for the FPL AI Agent dashboard.
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
from fpl.auth import FPLAuth
from ml.features import FeatureEngineer
from ml.predictor import get_predictor, HeuristicPredictor
from engine.captain import CaptainPicker
from engine.lineup import LineupOptimizer
from engine.transfers import TransferEngine
from engine.differentials import DifferentialFinder
from database.crud import DatabaseManager

# Initialize components
db = DatabaseManager()
fpl_auth = FPLAuth()
fpl_client = FPLClient(auth=fpl_auth)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    logger.info("FPL Agent API starting up...")
    yield
    logger.info("FPL Agent API shutting down...")


# Create FastAPI app
app = FastAPI(
    title="FPL AI Agent",
    description="AI-powered Fantasy Premier League agent",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Pydantic Models ====================

class LoginRequest(BaseModel):
    email: str
    password: str


class SettingsUpdate(BaseModel):
    auto_execute: Optional[bool] = None
    differential_mode: Optional[bool] = None
    notification_email: Optional[str] = None


class TransferRequest(BaseModel):
    player_out_id: int
    player_in_id: int


class LineupRequest(BaseModel):
    starting_ids: List[int]
    bench_ids: List[int]
    captain_id: int
    vice_captain_id: int


# ==================== Health Check ====================

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "authenticated": fpl_auth.is_authenticated
    }


# ==================== Authentication ====================

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    """Login to FPL."""
    try:
        fpl_auth.email = request.email
        fpl_auth.password = request.password
        
        success = fpl_auth.login()
        
        if success:
            # Store credentials
            db.set_setting("fpl_email", request.email)
            db.set_setting("fpl_team_id", str(fpl_auth.team_id))
            
            return {
                "success": True,
                "team_id": fpl_auth.team_id,
                "message": "Login successful"
            }
        else:
            raise HTTPException(status_code=401, detail="Login failed")
            
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auth/status")
async def auth_status():
    """Get authentication status."""
    return {
        "authenticated": fpl_auth.is_authenticated,
        "team_id": fpl_auth.team_id
    }


@app.post("/api/auth/logout")
async def logout():
    """Logout from FPL."""
    fpl_auth.logout()
    return {"success": True}


# ==================== Team Data ====================

@app.get("/api/team/current")
async def get_current_team():
    """Get current team."""
    if not fpl_auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        my_team = fpl_client.get_my_team()
        players = fpl_client.get_players()
        player_dict = {p.id: p for p in players}
        
        team_data = []
        for pick in my_team.picks:
            player = player_dict.get(pick.element)
            if player:
                team_data.append({
                    "id": player.id,
                    "name": player.web_name,
                    "team": player.team,
                    "position": player.position,
                    "price": player.price,
                    "points": player.total_points,
                    "form": float(player.form),
                    "is_captain": pick.is_captain,
                    "is_vice_captain": pick.is_vice_captain,
                    "is_starter": pick.position <= 11,
                    "bench_order": pick.position - 11 if pick.position > 11 else None
                })
        
        return {
            "players": team_data,
            "captain_id": my_team.captain_id
        }
        
    except Exception as e:
        logger.error(f"Error getting team: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/team/info")
async def get_team_info():
    """Get team info and stats."""
    if not fpl_auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        info = fpl_client.get_my_team_info()
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Predictions ====================

@app.get("/api/predictions")
async def get_predictions(top_n: int = 50):
    """Get player predictions for next gameweek."""
    try:
        # Get all players
        players = fpl_client.get_players()
        
        # Use heuristic predictor (fast)
        predictor = HeuristicPredictor()
        feature_eng = FeatureEngineer(fpl_client)
        
        predictions = []
        for player in players:
            if player.minutes < 90:  # Skip players with no minutes
                continue
            
            try:
                features = feature_eng.extract_features(
                    player.id,
                    include_history=False
                )
                pred = predictor.predict_player(features)
                
                predictions.append({
                    "player_id": player.id,
                    "name": player.web_name,
                    "team": player.team,
                    "position": player.position,
                    "price": player.price,
                    "predicted_points": round(pred, 2),
                    "form": float(player.form),
                    "ownership": float(player.selected_by_percent)
                })
            except Exception:
                continue
        
        # Sort by predicted points
        predictions.sort(key=lambda x: x["predicted_points"], reverse=True)
        
        return {"predictions": predictions[:top_n]}
        
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Recommendations ====================

@app.get("/api/recommendations/captain")
async def get_captain_recommendation():
    """Get captain recommendation."""
    if not fpl_auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        # Get team predictions
        my_team = fpl_client.get_my_team()
        team_ids = [p.element for p in my_team.picks]
        
        predictor = HeuristicPredictor()
        feature_eng = FeatureEngineer(fpl_client)
        
        team_predictions = []
        player_ownership = {}
        
        for player_id in team_ids:
            player = fpl_client.get_player(player_id)
            if player:
                features = feature_eng.extract_features(player_id, include_history=False)
                pred = predictor.predict_player(features)
                team_predictions.append((player_id, player.web_name, pred))
                player_ownership[player_id] = float(player.selected_by_percent)
        
        # Get captain pick
        picker = CaptainPicker()
        captain_pick = picker.pick(
            team_predictions,
            player_ownership,
            prefer_differential=db.get_setting("differential_mode") == "true"
        )
        
        return {
            "captain": {
                "id": captain_pick.captain_id,
                "name": captain_pick.captain_name,
                "predicted": round(captain_pick.captain_predicted, 2)
            },
            "vice_captain": {
                "id": captain_pick.vice_captain_id,
                "name": captain_pick.vice_captain_name,
                "predicted": round(captain_pick.vice_captain_predicted, 2)
            },
            "reasoning": captain_pick.reasoning,
            "options": picker.get_captain_options(team_predictions, player_ownership)
        }
        
    except Exception as e:
        logger.error(f"Captain recommendation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/recommendations/transfers")
async def get_transfer_recommendations():
    """Get transfer recommendations."""
    if not fpl_auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        # Get current team
        my_team = fpl_client.get_my_team()
        team_info = fpl_client.get_my_team_info()
        
        # Get all players
        all_players = fpl_client.get_players()
        
        predictor = HeuristicPredictor()
        feature_eng = FeatureEngineer(fpl_client)
        
        # Build current team data
        current_team = []
        for pick in my_team.picks:
            player = fpl_client.get_player(pick.element)
            if player:
                features = feature_eng.extract_features(player.id, include_history=False)
                pred = predictor.predict_player(features)
                current_team.append((
                    player.id,
                    player.web_name,
                    player.price,
                    player.element_type,
                    pred
                ))
        
        # Build all players data
        all_player_data = []
        for player in all_players:
            if player.minutes >= 90:
                try:
                    features = feature_eng.extract_features(player.id, include_history=False)
                    pred = predictor.predict_player(features)
                    all_player_data.append((
                        player.id,
                        player.web_name,
                        player.price,
                        player.element_type,
                        pred
                    ))
                except:
                    continue
        
        # Get budget
        budget = team_info.get("last_deadline_bank", 0) / 10
        free_transfers = team_info.get("last_deadline_total_transfers", 1)
        
        # Get transfer suggestions
        engine = TransferEngine()
        plan = engine.suggest_transfers(
            current_team,
            all_player_data,
            budget,
            free_transfers=free_transfers
        )
        
        return {
            "transfers": [
                {
                    "out": {
                        "id": t.player_out_id,
                        "name": t.player_out_name,
                        "price": t.player_out_price,
                        "predicted": round(t.player_out_predicted, 2)
                    },
                    "in": {
                        "id": t.player_in_id,
                        "name": t.player_in_name,
                        "price": t.player_in_price,
                        "predicted": round(t.player_in_predicted, 2)
                    },
                    "gain": round(t.points_gain, 2)
                }
                for t in plan.transfers
            ],
            "total_gain": round(plan.total_points_gain, 2),
            "transfer_cost": plan.total_cost,
            "net_gain": round(plan.net_gain, 2),
            "reasoning": plan.reasoning,
            "budget": budget,
            "free_transfers": free_transfers
        }
        
    except Exception as e:
        logger.error(f"Transfer recommendation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/recommendations/differentials")
async def get_differentials():
    """Get differential picks."""
    try:
        players = fpl_client.get_players()
        teams = fpl_client.get_teams()
        team_names = {t.id: t.short_name for t in teams}
        
        predictor = HeuristicPredictor()
        feature_eng = FeatureEngineer(fpl_client)
        
        # Get predictions for all players
        all_predictions = []
        player_data = {}
        
        for player in players:
            if player.minutes >= 180:  # At least 2 games
                try:
                    features = feature_eng.extract_features(player.id, include_history=False)
                    pred = predictor.predict_player(features)
                    all_predictions.append((player.id, player.web_name, pred))
                    player_data[player.id] = {
                        "selected_by_percent": player.selected_by_percent,
                        "form": player.form,
                        "team": player.team,
                        "element_type": player.element_type,
                        "now_cost": player.now_cost,
                        "minutes": player.minutes
                    }
                except:
                    continue
        
        # Find differentials
        finder = DifferentialFinder()
        differentials = finder.find_differentials(
            all_predictions,
            player_data,
            team_names
        )
        
        return {
            "differentials": [
                {
                    "player_id": d.player_id,
                    "name": d.name,
                    "team": d.team,
                    "position": d.position,
                    "price": d.price,
                    "predicted": round(d.predicted_points, 2),
                    "ownership": round(d.ownership, 1),
                    "form": round(d.form, 1),
                    "risk": d.risk_level,
                    "reasoning": d.reasoning
                }
                for d in differentials
            ]
        }
        
    except Exception as e:
        logger.error(f"Differential error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Actions ====================

@app.post("/api/actions/set-lineup")
async def set_lineup(request: LineupRequest):
    """Set team lineup."""
    if not fpl_auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        result = fpl_client.set_lineup(
            starting_ids=request.starting_ids,
            bench_ids=request.bench_ids,
            captain_id=request.captain_id,
            vice_captain_id=request.vice_captain_id
        )
        
        # Log decision
        next_gw = fpl_client.get_next_gameweek()
        if next_gw:
            db.log_decision(
                gameweek=next_gw.id,
                decision_type="lineup",
                details={
                    "starting": request.starting_ids,
                    "bench": request.bench_ids,
                    "captain": request.captain_id,
                    "vice_captain": request.vice_captain_id
                },
                reasoning="Manual lineup set via dashboard"
            )
        
        return {"success": True, "result": result}
        
    except Exception as e:
        logger.error(f"Set lineup error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== History ====================

@app.get("/api/history/decisions")
async def get_decision_history(limit: int = 20):
    """Get decision history."""
    decisions = db.get_decisions(limit=limit)
    return {"decisions": decisions}


@app.get("/api/history/performance")
async def get_performance_history():
    """Get performance history."""
    performance = db.get_performance_history()
    return {"history": performance}


# ==================== Settings ====================

@app.get("/api/settings")
async def get_settings():
    """Get agent settings."""
    settings = db.get_all_settings()
    return {
        "auto_execute": settings.get("auto_execute") == "true",
        "differential_mode": settings.get("differential_mode") == "true",
        "notification_email": settings.get("notification_email", ""),
        "fpl_email": settings.get("fpl_email", ""),
        "fpl_team_id": settings.get("fpl_team_id", "")
    }


@app.post("/api/settings")
async def update_settings(settings: SettingsUpdate):
    """Update agent settings."""
    if settings.auto_execute is not None:
        db.set_setting("auto_execute", str(settings.auto_execute).lower())
    if settings.differential_mode is not None:
        db.set_setting("differential_mode", str(settings.differential_mode).lower())
    if settings.notification_email is not None:
        db.set_setting("notification_email", settings.notification_email)
    
    return {"success": True}


# ==================== FPL Data ====================

@app.get("/api/fpl/gameweek")
async def get_current_gameweek():
    """Get current/next gameweek info."""
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
                "deadline": next_gw.deadline_time.isoformat() if next_gw else None
            } if next_gw else None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/fpl/players")
async def get_all_players(position: Optional[int] = None, top_n: int = 100):
    """Get all players."""
    try:
        if position:
            players = fpl_client.get_players_by_position(position)
        else:
            players = fpl_client.get_top_players(n=top_n)
        
        return {
            "players": [
                {
                    "id": p.id,
                    "name": p.web_name,
                    "full_name": p.full_name,
                    "team": p.team,
                    "position": p.position,
                    "price": p.price,
                    "points": p.total_points,
                    "form": float(p.form),
                    "ownership": float(p.selected_by_percent),
                    "status": p.status,
                    "news": p.news
                }
                for p in players
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

