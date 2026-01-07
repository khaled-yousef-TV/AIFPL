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
import requests

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import response models for better API documentation
from .response_models import (
    GameWeekResponse, SuggestedSquadResponse, SavedSquadsResponse,
    SavedSquadResponse, SaveSquadResponse, DeleteSquadResponse,
    HealthResponse, BettingOddsDebugResponse,
    FplTeamsResponse, SaveFplTeamResponse
)
from dotenv import load_dotenv
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from datetime import timedelta

# Configure logging first (needed for messages below)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Validate environment variables (after logging is set up)
try:
    from .config import validate_env
    validate_env()
except (ImportError, ValueError) as e:
    logger.warning(f"Environment validation skipped: {e}")

# Load .env from backend directory (where uvicorn typically runs from)
# Try multiple paths to ensure we find it
backend_dir = Path(__file__).parent.parent  # Goes from api/main.py to backend/
env_paths = [
    backend_dir / '.env',  # backend/.env (most common case)
    Path.cwd() / '.env',   # Current working directory
    Path.cwd() / 'backend' / '.env',  # If running from project root
]

logger.info(f"Looking for .env file...")
logger.info(f"  Current working directory: {Path.cwd()}")
logger.info(f"  Backend directory (from __file__): {backend_dir}")
logger.info(f"  Checking paths: {[str(p) for p in env_paths]}")

env_loaded = False
for env_path in env_paths:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)  # Use override=True to ensure it's loaded
        logger.info(f"✓ Loaded .env from: {env_path.absolute()}")
        # Verify it was loaded
        test_key = os.getenv("THE_ODDS_API_KEY")
        test_enabled = os.getenv("BETTING_ODDS_ENABLED")
        logger.info(f"  Verified: THE_ODDS_API_KEY={'SET' if test_key else 'NOT SET'}, BETTING_ODDS_ENABLED={test_enabled}")
        env_loaded = True
        break

if not env_loaded:
    # Fallback: try loading from current directory (standard behavior)
    load_dotenv()
    test_key = os.getenv("THE_ODDS_API_KEY")
    test_enabled = os.getenv("BETTING_ODDS_ENABLED")
    logger.warning(f"Using default load_dotenv() - .env file might not be in expected location")
    logger.warning(f"  Current env vars: THE_ODDS_API_KEY={'SET' if test_key else 'NOT SET'}, BETTING_ODDS_ENABLED={test_enabled}")

# Import our modules
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpl.client import FPLClient
from ml.features import FeatureEngineer
from ml.predictor import HeuristicPredictor, FormPredictor, FixturePredictor
from data.european_teams import assess_rotation_risk, get_european_competition
from data.trends import compute_team_trends
from data.betting_odds import BettingOddsClient
from database.crud import DatabaseManager

# Import constants - handle both relative and absolute imports
try:
    from constants import PlayerStatus, PlayerPosition
except ImportError:
    # Fallback for when running from different directory structure
    import sys
    import os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from constants import PlayerStatus, PlayerPosition

# Initialize components (no auth needed for public data)
fpl_client = FPLClient(auth=None)
predictor_heuristic = HeuristicPredictor()
predictor_form = FormPredictor()
predictor_fixture = FixturePredictor()
feature_eng = FeatureEngineer(fpl_client)

# Initialize betting odds client (with debug logging)
logger.info(f"Initializing BettingOddsClient...")
logger.info(f"Environment check: THE_ODDS_API_KEY={'SET' if os.getenv('THE_ODDS_API_KEY') else 'NOT SET'}, BETTING_ODDS_ENABLED={os.getenv('BETTING_ODDS_ENABLED', 'NOT SET')}")
betting_odds_client = BettingOddsClient()
logger.info(f"BettingOddsClient initialized: enabled={betting_odds_client.enabled}, has_key={bool(betting_odds_client.api_key)}")

# Initialize database manager
db_manager = DatabaseManager()

# Simple in-memory caches to keep the UI snappy (especially in dev with single uvicorn worker)
_CACHE_TTL_SECONDS = int(os.getenv("FPL_CACHE_TTL_SECONDS", "300"))
_cache_lock = Lock()
_cache: Dict[str, Dict[Any, Any]] = {
    "predictions": {},  # key -> (ts, list)
    "squad": {},        # key -> (ts, dict)
}


def _cache_get(namespace: str, key: Any) -> Optional[Any]:
    with _cache_lock:
        item = _cache.get(namespace, {}).get(key)
        if not item:
            return None
        ts, data = item
        if time() - ts > _CACHE_TTL_SECONDS:
            _cache[namespace].pop(key, None)
            return None
        return data


def _cache_set(namespace: str, key: Any, data: Any) -> None:
    with _cache_lock:
        _cache.setdefault(namespace, {})[key] = (time(), data)


# Create FastAPI app
app = FastAPI(
    title="FPL Squad Suggester",
    description="AI-powered squad suggestions for Fantasy Premier League",
    version="1.0.0",
)

# CORS - configurable via environment variable
allowed_origins_str = os.getenv(
    "CORS_ORIGINS",
    "https://fplai.nl,https://www.fplai.nl,http://localhost:3000,http://127.0.0.1:3000"
)
allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    # Render backend will be called by GitHub Pages frontend at https://fplai.nl
    # Keep dev localhost allowed too.
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include chip optimization routes
from api.routes import chips as chips_router
chips_router.initialize_chips_router(fpl_client, feature_eng)
app.include_router(chips_router.router, prefix="/api/chips", tags=["chips"])


# ==================== Scheduler for Auto-Saving Selected Teams ====================

scheduler = BackgroundScheduler()

def save_selected_team_job():
    """Job to save selected team 30 minutes before deadline (sync wrapper for scheduler)."""
    import asyncio
    try:
        # Run the async function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_save_selected_team_async())
        loop.close()
    except Exception as e:
        logger.error(f"Error in save_selected_team_job: {e}")

async def _save_selected_team_async():
    """Async function to save selected team."""
    try:
        next_gw = fpl_client.get_next_gameweek()
        if not next_gw:
            logger.warning("No next gameweek found for selected team save job")
            return
        
        # Check if already saved
        existing = db_manager.get_selected_team(next_gw.id)
        if existing:
            logger.info(f"Selected team for GW{next_gw.id} already saved, skipping")
            return
        
        # Get the current combined squad suggestion
        squad_data = await get_suggested_squad(budget=100.0, method="combined")
        
        # Save to database
        success = db_manager.save_selected_team(next_gw.id, squad_data)
        if success:
            logger.info(f"Successfully saved selected team for Gameweek {next_gw.id} (30 min before deadline)")
        else:
            logger.error(f"Failed to save selected team for Gameweek {next_gw.id}")
    except Exception as e:
        logger.error(f"Error in _save_selected_team_async: {e}")

def save_daily_snapshot_job():
    """Job to save daily snapshot at midnight (sync wrapper for scheduler)."""
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_save_daily_snapshot_async())
        loop.close()
    except Exception as e:
        logger.error(f"Error in save_daily_snapshot_job: {e}")

async def _save_daily_snapshot_async():
    """Async function to save daily snapshot."""
    try:
        next_gw = fpl_client.get_next_gameweek()
        if not next_gw:
            logger.warning("No next gameweek found for daily snapshot save job")
            return
        
        # Force refresh FPL data to get latest player status before generating squad
        logger.info("Forcing FPL data refresh before daily snapshot generation")
        fpl_client.get_bootstrap(force_refresh=True)
        
        # Get the current combined squad suggestion with refresh enabled
        squad_data = await get_suggested_squad(budget=100.0, method="combined", refresh=True)
        
        # Validate squad doesn't contain unavailable/doubtful players and regenerate if needed
        # (max 2 attempts to avoid infinite loop)
        max_attempts = 2
        for attempt in range(max_attempts):
            invalid_players = []
            all_player_ids = []
            for player in squad_data.get("starting_xi", []) + squad_data.get("bench", []):
                all_player_ids.append(player.get("id"))
            
            # Get fresh player data to validate
            players = fpl_client.get_players()
            player_dict = {p.id: p for p in players}
            
            for pid in all_player_ids:
                player = player_dict.get(pid)
                if not player:
                    continue
                # Check status (exclude injured, suspended, unavailable, not available, AND doubtful)
                if player.status in [PlayerStatus.INJURED, PlayerStatus.SUSPENDED, 
                                    PlayerStatus.UNAVAILABLE, PlayerStatus.NOT_AVAILABLE, PlayerStatus.DOUBTFUL]:
                    invalid_players.append(f"{player.web_name} {player.second_name} (status: {player.status})")
                    continue
                # Check chance of playing
                chance = player.chance_of_playing_next_round
                if chance is not None and chance < 50:
                    invalid_players.append(f"{player.web_name} {player.second_name} (chance: {chance}%)")
                    continue
                # Check news field
                news_lower = (player.news or "").lower()
                if any(keyword in news_lower for keyword in ["injured", "injury", "suspended", "unavailable", "ruled out", "will miss", "out for"]):
                    invalid_players.append(f"{player.web_name} {player.second_name} (news: {player.news[:50]})")
            
            if not invalid_players:
                # Squad is valid, break out of validation loop
                break
            
            if attempt < max_attempts - 1:
                logger.warning(f"Daily snapshot contains {len(invalid_players)} invalid players: {', '.join(invalid_players)}")
                logger.warning(f"Regenerating squad (attempt {attempt + 1}/{max_attempts})...")
                # Force another refresh and regenerate
                fpl_client.get_bootstrap(force_refresh=True)
                squad_data = await get_suggested_squad(budget=100.0, method="combined", refresh=True)
            else:
                # Final attempt still has invalid players - log warning but save anyway
                logger.error(f"Daily snapshot still contains {len(invalid_players)} invalid players after {max_attempts} attempts: {', '.join(invalid_players)}")
                logger.error("Saving snapshot anyway, but squad may contain unavailable/doubtful players")
        
        # Save daily snapshot (always create new entry)
        success = db_manager.save_daily_snapshot(next_gw.id, squad_data)
        if success:
            logger.info(f"Successfully saved daily snapshot for Gameweek {next_gw.id} at midnight")
        else:
            logger.error(f"Failed to save daily snapshot for Gameweek {next_gw.id}")
        
        # Calculate Triple Captain recommendations (runs in background)
        try:
            logger.info(f"Starting Triple Captain calculation for GW{next_gw.id} as part of daily snapshot job")
            chips_router._calculate_triple_captain_background(next_gw.id)
            logger.info(f"Triple Captain calculation queued for GW{next_gw.id}")
        except Exception as tc_error:
            logger.error(f"Error calculating Triple Captain recommendations in daily snapshot job: {tc_error}")
            # Don't fail the entire job if triple captain calculation fails
    except Exception as e:
        logger.error(f"Error in _save_daily_snapshot_async: {e}")

def schedule_next_save():
    """Schedule the next selected team save job 30 minutes before deadline."""
    try:
        next_gw = fpl_client.get_next_gameweek()
        if not next_gw or not next_gw.deadline_time:
            logger.warning("No next gameweek deadline found")
            return
        
        deadline_str = next_gw.deadline_time
        # Parse deadline (should be datetime object from Pydantic model)
        if isinstance(deadline_str, datetime):
            deadline = deadline_str
        elif isinstance(deadline_str, str):
            # Handle ISO format with timezone
            deadline_str = deadline_str.replace('Z', '+00:00')
            try:
                deadline = datetime.fromisoformat(deadline_str)
            except ValueError:
                # Fallback: try parsing common formats (requires python-dateutil)
                try:
                    from dateutil import parser
                    deadline = parser.parse(deadline_str)
                except ImportError:
                    logger.error("dateutil not installed, cannot parse deadline string")
                    return
        else:
            deadline = deadline_str
        
        # Calculate 30 minutes before deadline
        save_time = deadline - timedelta(minutes=30)
        now = datetime.now(deadline.tzinfo) if hasattr(deadline, 'tzinfo') and deadline.tzinfo else datetime.now()
        
        # Check if we're already past the save time
        if save_time <= now:
            # Save immediately if past the 30-minute mark
            logger.info(f"Already past 30 min before deadline for GW{next_gw.id}, saving immediately")
            save_selected_team_job()
            return
        
        # Remove existing job if any
        try:
            scheduler.remove_job("save_selected_team")
        except (KeyError, ValueError):
            # Job doesn't exist, which is fine
            pass
        
        # Schedule the job (sync function)
        scheduler.add_job(
            save_selected_team_job,
            DateTrigger(run_date=save_time),
            id="save_selected_team",
            name="Save Selected Team 30min Before Deadline",
            replace_existing=True
        )
        
        logger.info(f"Scheduled selected team save for GW{next_gw.id} at {save_time} (30 min before deadline)")
    except Exception as e:
        logger.error(f"Error scheduling selected team save: {e}")

async def check_and_run_missed_saves():
    """Check if we missed any saves while the server was down and run them."""
    try:
        next_gw = fpl_client.get_next_gameweek()
        if not next_gw or not next_gw.deadline_time:
            return
        
        # Check if we missed the 30-min-before-deadline save
        deadline_str = next_gw.deadline_time
        if isinstance(deadline_str, datetime):
            deadline = deadline_str
        elif isinstance(deadline_str, str):
            deadline_str = deadline_str.replace('Z', '+00:00')
            try:
                deadline = datetime.fromisoformat(deadline_str)
            except ValueError:
                try:
                    from dateutil import parser
                    deadline = parser.parse(deadline_str)
                except ImportError:
                    return
        else:
            deadline = deadline_str
        
        save_time = deadline - timedelta(minutes=30)
        now = datetime.now(deadline.tzinfo) if hasattr(deadline, 'tzinfo') and deadline.tzinfo else datetime.now()
        
        # If we're past the save time but before deadline, and haven't saved yet
        if save_time <= now < deadline:
            existing = db_manager.get_selected_team(next_gw.id)
            if not existing:
                logger.info(f"Server woke up after scheduled save time but before deadline. Running missed save for GW{next_gw.id}")
                await _save_selected_team_async()
        
        # Check if we missed today's midnight snapshot (run if it's past midnight and we haven't saved today)
        try:
            today = datetime.utcnow().date()
            latest_snapshot = db_manager.get_latest_daily_snapshot(next_gw.id)
            if latest_snapshot and latest_snapshot.get('saved_at'):
                snapshot_date_str = latest_snapshot['saved_at']
                if isinstance(snapshot_date_str, str):
                    snapshot_date_str = snapshot_date_str.replace('Z', '+00:00')
                    snapshot_dt = datetime.fromisoformat(snapshot_date_str)
                    snapshot_date = snapshot_dt.date()
                    if snapshot_date < today:
                        logger.info(f"Server woke up after midnight. Running missed daily snapshot for GW{next_gw.id}")
                        await _save_daily_snapshot_async()
            elif not latest_snapshot:
                # No snapshot exists, save one now
                logger.info(f"No daily snapshot exists for GW{next_gw.id}. Creating one now.")
                await _save_daily_snapshot_async()
        except Exception as snapshot_error:
            logger.error(f"Error checking daily snapshot: {snapshot_error}")
            
    except Exception as e:
        logger.error(f"Error checking missed saves: {e}")

@app.on_event("startup")
async def startup_event():
    """Start the scheduler on app startup."""
    import asyncio
    scheduler.start()
    logger.info("Selected team scheduler started")
    
    # Check for missed saves when server wakes up (for Render free tier spin-down scenario)
    await check_and_run_missed_saves()
    
    # Schedule the first save job (30 min before deadline)
    schedule_next_save()
    # Also schedule a check every 6 hours to reschedule if needed
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        schedule_next_save,
        CronTrigger(hour="*/6"),  # Every 6 hours
        id="check_and_schedule_selected_team",
        name="Check and Schedule Selected Team Save",
        replace_existing=True
    )
    # Schedule daily snapshot at midnight (00:00)
    scheduler.add_job(
        save_daily_snapshot_job,
        CronTrigger(hour=0, minute=0),  # Every day at midnight
        id="save_daily_snapshot",
        name="Save Daily Snapshot at Midnight",
        replace_existing=True
    )
    logger.info("Scheduled daily snapshot job for midnight")

@app.on_event("shutdown")
async def shutdown_event():
    """Stop the scheduler on app shutdown."""
    scheduler.shutdown()
    logger.info("Selected team scheduler stopped")


# ==================== Health Check ====================

@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.
    Can also be used to wake up the server on Render free tier.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }

@app.get("/api/betting-odds-status")
async def get_betting_odds_status():
    """Debug endpoint to check betting odds configuration."""
    return {
        "enabled": betting_odds_client.enabled,
        "has_api_key": bool(betting_odds_client.api_key),
        "weight": betting_odds_client.weight,
        "api_key_set": bool(os.getenv("THE_ODDS_API_KEY")),
        "enabled_env": os.getenv("BETTING_ODDS_ENABLED", "false"),
    }


# ==================== Gameweek Info ====================

@app.get("/api/gameweek", response_model=GameWeekResponse)
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
                if player.status in ["i", "s", "u", "n"]:
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
    budget: float = 100.0,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """Build squad using a specific predictor method."""
    # Force refresh FPL data if requested
    if force_refresh:
        fpl_client.get_bootstrap(force_refresh=True)
    
    next_gw = fpl_client.get_next_gameweek()
    gw_id = next_gw.id if next_gw else 0
    cache_key = (method_name, gw_id, round(budget, 1))
    
    # Skip cache if forcing refresh
    if not force_refresh:
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
    
    # Team trend/reversal signals (computed from all finished fixtures so far)
    try:
        all_fixtures = fpl_client.get_fixtures(gameweek=None)
        team_trends = compute_team_trends(teams, all_fixtures, window=6, previous_window=6)
    except Exception:
        team_trends = {}
    
    # Fetch betting odds for fixtures (cache fixture odds by team)
    fixture_odds_cache = {}
    odds_fetched_count = 0
    if betting_odds_client.enabled:
        try:
            logger.info(f"Fetching betting odds for {len(fixtures)} fixtures...")
            # Fetch all odds once (more efficient)
            all_odds_data = betting_odds_client._fetch_all_odds()
            
            if all_odds_data:
                logger.info(f"Retrieved {len(all_odds_data)} fixtures from betting API")
                # Match each FPL fixture to betting odds
                unmatched = []
                for f in fixtures:
                    home_team = team_names.get(f.team_h, "???")
                    away_team = team_names.get(f.team_a, "???")
                    odds = betting_odds_client.get_fixture_odds(home_team, away_team, all_odds_data)
                    if odds:
                        fixture_odds_cache[f.team_h] = {**odds, "is_home": True}
                        fixture_odds_cache[f.team_a] = {**odds, "is_home": False}
                        odds_fetched_count += 1
                    else:
                        unmatched.append(f"{home_team} vs {away_team}")
                
                logger.info(f"Matched betting odds for {odds_fetched_count}/{len(fixtures)} fixtures")
                if unmatched:
                    logger.info(f"Unmatched fixtures: {', '.join(unmatched)}")
            else:
                logger.warning("Could not fetch odds from betting API")
        except Exception as e:
            logger.warning(f"Error fetching betting odds: {e}. Continuing without odds.")
            import traceback
            logger.debug(traceback.format_exc())
            fixture_odds_cache = {}
    else:
        logger.info("Betting odds disabled (not enabled or no API key)")

    player_predictions = []
    for player in players:
        # Allow players with at least 1 minute (includes new signings, rotation players)
        if player.minutes < 1:
            continue
        # Skip unavailable players (injured/suspended/not available/doubtful)
        # For free hit team, we exclude doubtful players as well to ensure reliability
        if player.status in [PlayerStatus.INJURED, PlayerStatus.SUSPENDED, 
                             PlayerStatus.UNAVAILABLE, PlayerStatus.NOT_AVAILABLE, PlayerStatus.DOUBTFUL]:
            continue
        # Also filter by chance_of_playing_next_round - if it's None or < 50%, exclude
        # This catches cases where FPL API status is "a" but player is actually unavailable
        chance = player.chance_of_playing_next_round
        if chance is not None and chance < 50:
            continue
        # Check news field for injury/suspension keywords (FPL sometimes doesn't update status immediately)
        news_lower = (player.news or "").lower()
        if any(keyword in news_lower for keyword in ["injured", "injury", "suspended", "unavailable", "ruled out", "will miss", "out for"]):
            continue
        
        # For free hit: Check recent playing time to exclude players who haven't played recently
        # This catches cases like backup goalkeepers who played early season but not recently
        try:
            player_details = fpl_client.get_player_details(player.id)
            history = player_details.get("history", [])
            if history:
                # Get last 3 gameweeks of history (most recent first)
                # Filter to only finished gameweeks (round > 0 and has been played)
                finished_gws = [gw for gw in history if gw.get("round", 0) > 0]
                if finished_gws:
                    # Sort by round descending (most recent first)
                    finished_gws.sort(key=lambda x: x.get("round", 0), reverse=True)
                    recent_gws = finished_gws[:3]
                    # Calculate average minutes in last 3 gameweeks
                    recent_minutes = [gw.get("minutes", 0) for gw in recent_gws]
                    if recent_minutes:
                        avg_recent_minutes = sum(recent_minutes) / len(recent_minutes)
                        # Filter out players who average less than 30 minutes in recent gameweeks
                        # This excludes backup players who haven't been playing
                        if avg_recent_minutes < 30:
                            # Exception: Allow if they played in the most recent gameweek (might be returning from injury/rotation)
                            most_recent_minutes = recent_minutes[0] if recent_minutes else 0
                            if most_recent_minutes < 1:
                                logger.debug(f"Filtering {player.web_name} (avg recent minutes: {avg_recent_minutes:.1f}, most recent: {most_recent_minutes})")
                                continue
        except Exception as e:
            # If we can't get history, log but don't block (might be rate limiting or API issue)
            logger.debug(f"Could not check recent minutes for player {player.id} ({player.web_name}): {e}")
            # In this case, we'll be more lenient and allow the player through
        
        try:
            features = feature_eng.extract_features(player.id, include_history=False)
            pred = predictor.predict_player(features)
            
            fix = fixture_info.get(player.team, {})
            opponent = fix.get("opponent", "???")
            difficulty = fix.get("difficulty", 3)
            is_home = fix.get("is_home", False)
            
            team_name = team_names.get(player.team, "???")
            rotation = assess_rotation_risk(team_name, gw_deadline, difficulty)
            trend = team_trends.get(player.team)
            reversal = trend.reversal_score if trend else 0.0
            
            # Get betting odds for this fixture
            odds_data = fixture_odds_cache.get(player.team, {})
            anytime_goalscorer_prob = 0.0
            clean_sheet_prob = 0.0
            team_win_prob = 0.5
            
            if odds_data:
                # Phase 2: Enhanced odds with player stats
                if player.element_type in [PlayerPosition.MID, PlayerPosition.FWD]:
                    # Calculate player stats for better goalscorer estimation
                    games_played = max(1, player.minutes / 90.0) if player.minutes > 0 else 1
                    goals_per_game = player.goals_scored / games_played
                    xg_per_game = float(player.expected_goals) / games_played
                    
                    player_stats = {
                        "goals_per_game": goals_per_game,
                        "xg_per_game": xg_per_game,
                        "position": player.element_type,
                        "is_premium": player.price >= 9.0
                    }
                    
                    anytime_goalscorer_prob = betting_odds_client.get_player_goalscorer_odds(
                        player.web_name, odds_data, player_stats
                    )
                elif player.element_type in [PlayerPosition.GK, PlayerPosition.DEF]:
                    clean_sheet_prob = betting_odds_client.get_clean_sheet_probability(
                        is_home, odds_data
                    )
                
                # Team win probability
                team_win_prob = odds_data.get("home_win_prob" if is_home else "away_win_prob", 0.5)
            
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
            if reversal >= 1.2:
                reasons.append(f"Bounce-back spot ({team_name})")
            
            if not reasons:
                reasons.append(f"vs {opponent} ({'H' if is_home else 'A'})")
            
            # Map position_id to position string
            position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
            position_str = position_map.get(player.element_type, "MID")
            
            player_predictions.append({
                "id": player.id,
                "name": player.web_name,
                "team": team_name,
                "team_id": player.team,
                "position": position_str,
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
                "team_reversal": reversal,
                "status": player.status,  # Include status for filtering in squad builder
                # Betting odds probabilities
                "anytime_goalscorer_prob": anytime_goalscorer_prob,
                "clean_sheet_prob": clean_sheet_prob,
                "team_win_prob": team_win_prob,
                "reason": " • ".join(reasons[:2]),
            })
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            logger.debug(f"Skipping player {player.id} due to error: {e}")
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
async def get_suggested_squad(budget: float = 100.0, method: str = "combined", refresh: bool = False):
    """
    Get optimal suggested squad for next gameweek.
    
    Args:
        budget: Total budget in millions (default 100.0)
        method: Prediction method - "heuristic", "form", "fixture", or "combined" (default)
        refresh: Force refresh FPL data cache (default False) - use this if player status seems stale
    """
    try:
        # Force refresh FPL data if requested (to get latest player status)
        if refresh:
            fpl_client.get_bootstrap(force_refresh=True)
        if method == "heuristic":
            return await _build_squad_with_predictor(predictor_heuristic, "Heuristic (Balanced)", budget, force_refresh=refresh)
        elif method == "form":
            return await _build_squad_with_predictor(predictor_form, "Form-Focused", budget, force_refresh=refresh)
        elif method == "fixture":
            return await _build_squad_with_predictor(predictor_fixture, "Fixture-Focused", budget, force_refresh=refresh)
        else:  # combined
            # Get predictions from all 3 methods
            heuristic_squad = await _build_squad_with_predictor(predictor_heuristic, "Heuristic", budget, force_refresh=refresh)
            form_squad = await _build_squad_with_predictor(predictor_form, "Form", budget, force_refresh=refresh)
            fixture_squad = await _build_squad_with_predictor(predictor_fixture, "Fixture", budget, force_refresh=refresh)
            
            # Average predictions for each player
            all_players = {}
            
            for squad in [heuristic_squad, form_squad, fixture_squad]:
                for player in squad["starting_xi"] + squad["bench"]:
                    pid = player["id"]
                    # Skip players that don't have status or are unavailable (safety check)
                    # Status: a=available, d=doubtful, i=injured, s=suspended, u=unavailable, n=not available
                    if player.get("status", "") in ["i", "s", "u", "n"]:
                        continue
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
                # Double-check status before including (safety check)
                # Status: a=available, d=doubtful, i=injured, s=suspended, u=unavailable, n=not available
                status = pdata.get("status", "")
                if status in ["i", "s", "u", "n"]:
                    continue
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
    # Filter out unavailable players (injured, suspended, unavailable, not available)
    # Status: a=available, d=doubtful, i=injured, s=suspended, u=unavailable, n=not available
    # This is a safety check in case status wasn't filtered earlier
    available_players = [
        p for p in players 
        if p.get("status", "a") not in ["i", "s", "u", "n"]
    ]
    
    by_position = {1: [], 2: [], 3: [], 4: []}
    for p in available_players:
        by_position[p["position_id"]].append(p)
    
    def player_score(p):
        pred = p["predicted"]
        pos_id = p.get("position_id")
        difficulty = p.get("difficulty", 3)
        form = p.get("form", 2)
        price = max(p["price"], 4.0)
        rotation_risk = p.get("rotation_risk", "none")
        rotation_factor = p.get("rotation_factor", 0)
        team_reversal = float(p.get("team_reversal", 0.0) or 0.0)
        
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
        
        # Team reversal bonus: strong teams that recently underperformed get a small uplift.
        # This helps "get ahead of templates" by catching trend reversals early.
        reversal_bonus = max(0.0, min(1.2, team_reversal)) * 0.6

        score = pred + fixture_bonus + form_bonus + home_bonus + rotation_penalty + reversal_bonus
        value_factor = pred / price

        # Premium attackers get extra weight due to captaincy ceiling.
        captain_uplift = 0.0
        if pos_id in (3, 4) and pred >= 6.0:
            captain_uplift = max(0.0, min(1.5, (pred - 6.0) * 0.6))

        # Reduce value-penalty for MID/FWD so we don't over-prefer cheap picks.
        value_weight = 0.18 if pos_id in (3, 4) else 0.30
        
        # Betting odds bonus (if enabled)
        odds_bonus = 0.0
        if betting_odds_client.enabled:
            odds_weight = betting_odds_client.weight
            
            if pos_id in (3, 4):  # MID/FWD
                # Anytime goalscorer probability
                goalscorer_prob = p.get("anytime_goalscorer_prob", 0.0)
                if goalscorer_prob > 0:
                    # Goal = 4-6 FPL points, so weight the probability accordingly
                    odds_bonus += goalscorer_prob * 4.0 * odds_weight
            
            elif pos_id in (1, 2):  # GK/DEF
                # Clean sheet probability
                cs_prob = p.get("clean_sheet_prob", 0.0)
                if cs_prob > 0:
                    # Clean sheet = 4 FPL points for DEF/GK
                    odds_bonus += cs_prob * 3.0 * odds_weight
            
            # Team win bonus (affects all positions - bonus points potential)
            team_win_prob = p.get("team_win_prob", 0.5)
            win_bonus = (team_win_prob - 0.5) * 0.3 * odds_weight  # Small bonus for favored teams
            odds_bonus += win_bonus
            
            # Log if odds bonus is significant (for debugging)
            if odds_bonus > 0.5:
                logger.debug(f"Player {p.get('name')} got odds bonus: {odds_bonus:.2f}")

        return score + captain_uplift + value_factor * value_weight + odds_bonus
    
    for pos in by_position:
        by_position[pos].sort(key=player_score, reverse=True)
    
    # Selection with budget reservation:
    # - Global greedy by predicted points (not "fill by position" order)
    # - Still respects FPL constraints and reserves budget to complete remaining slots.
    squad: List[Dict] = []
    selected_ids = set()
    team_counts: Dict[int, int] = {}
    remaining_budget = budget

    requirements = {1: 2, 2: 5, 3: 5, 4: 3}
    requirements_left = dict(requirements)

    # We'll use a consistent position order when estimating cheapest remaining costs.
    selection_order = [4, 3, 2, 1]

    # Cheapest-first lists per position for reserve estimation
    by_price = {
        pos: sorted(by_position[pos], key=lambda x: x.get("price", 999))
        for pos in by_position
    }

    def estimate_min_remaining_cost(req_left: Dict[int, int], sel_ids: set, counts: Dict[int, int]) -> float:
        temp_counts = dict(counts)
        temp_sel = set(sel_ids)
        cost = 0.0
        for pos in selection_order:
            need = int(req_left.get(pos, 0) or 0)
            if need <= 0:
                continue
            for cand in by_price.get(pos, []):
                if need <= 0:
                    break
                cid = cand.get("id")
                if cid in temp_sel:
                    continue
                tid = cand.get("team_id")
                if tid is None:
                    continue
                if temp_counts.get(tid, 0) >= 3:
                    continue
                cprice = float(cand.get("price", 999))
                cost += cprice
                temp_sel.add(cid)
                temp_counts[tid] = temp_counts.get(tid, 0) + 1
                need -= 1
            if need > 0:
                return float("inf")
        return cost

    # Global candidate order: highest predicted first (tie-breaker: player_score).
    all_candidates = sorted(
        players,
        key=lambda p: (float(p.get("predicted", 0)), float(player_score(p))),
        reverse=True,
    )

    # First pass: pick best predicted while leaving room to complete the squad.
    for cand in all_candidates:
        if sum(requirements_left.values()) <= 0:
            break

        pos_id = cand.get("position_id")
        if pos_id not in requirements_left or requirements_left[pos_id] <= 0:
            continue

        cid = cand.get("id")
        if cid in selected_ids:
            continue

        tid = cand.get("team_id")
        if tid is None:
            continue

        if team_counts.get(tid, 0) >= 3:
            continue

        cprice = float(cand.get("price", 999))
        if cprice > remaining_budget:
            continue

        # Budget reservation check
        req_sim = dict(requirements_left)
        req_sim[pos_id] = req_sim.get(pos_id, 0) - 1
        team_sim = dict(team_counts)
        team_sim[tid] = team_sim.get(tid, 0) + 1
        sel_sim = set(selected_ids)
        sel_sim.add(cid)
        rem = remaining_budget - cprice
        min_needed = estimate_min_remaining_cost(req_sim, sel_sim, team_sim)
        if rem >= min_needed:
            squad.append(cand)
            selected_ids.add(cid)
            team_counts[tid] = team_counts.get(tid, 0) + 1
            remaining_budget = rem
            requirements_left[pos_id] -= 1

    # Second pass: fill any missing slots with cheapest valid options.
    for pos_id in selection_order:
        while requirements_left.get(pos_id, 0) > 0:
            picked = False
            for cand in by_price.get(pos_id, []):
                cid = cand.get("id")
                if cid in selected_ids:
                    continue
                tid = cand.get("team_id")
                if tid is None or team_counts.get(tid, 0) >= 3:
                    continue
                cprice = float(cand.get("price", 999))
                if cprice > remaining_budget:
                    continue
                squad.append(cand)
                selected_ids.add(cid)
                team_counts[tid] = team_counts.get(tid, 0) + 1
                remaining_budget -= cprice
                requirements_left[pos_id] -= 1
                picked = True
                break
            if not picked:
                break
    
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


# ==================== Team Trends (Debug/QA) ====================

@app.get("/api/team-trends")
async def get_team_trends(window: int = 6, previous_window: int = 6):
    """Inspect team trend/reversal signals used by the suggester."""
    try:
        teams = fpl_client.get_teams()
        fixtures = fpl_client.get_fixtures(gameweek=None)
        trends = compute_team_trends(teams, fixtures, window=window, previous_window=previous_window)

        # Sort by reversal_score desc
        rows = sorted(trends.values(), key=lambda t: t.reversal_score, reverse=True)
        return {
            "window": window,
            "previous_window": previous_window,
            "teams": [
                {
                    "team": t.short_name,
                    "strength": t.strength,
                    "played": t.played,
                    "season_ppm": t.season_ppm,
                    "recent_ppm": t.recent_ppm,
                    "momentum": t.momentum,
                    "reversal_score": t.reversal_score,
                }
                for t in rows
            ],
        }
    except Exception as e:
        logger.error(f"Team trends error: {e}")
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
    suggestions_limit: int = 3  # How many transfer moves to return (hold suggestion may be added on top)


@app.post("/api/transfer-suggestions")
async def get_transfer_suggestions(request: TransferRequest):
    """
    Get transfer suggestions based on user's current squad.
    
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
        players_by_id = {p.id: p for p in players}
        
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
            except (AttributeError, KeyError, TypeError) as e:
                logger.debug(f"Error processing fixture: {e}")
                pass
        
        # Calculate average fixture difficulty for next 5 GWs
        avg_fixture_difficulty = {}
        for team_id, diffs in long_term_fixtures.items():
            avg_fixture_difficulty[team_id] = sum(diffs) / len(diffs) if diffs else 3.0

        # Phase 2: Fetch betting odds for transfer suggestions
        fixture_odds_cache = {}
        if betting_odds_client.enabled:
            try:
                logger.info(f"Fetching betting odds for transfer suggestions...")
                all_odds_data = betting_odds_client._fetch_all_odds()
                
                if all_odds_data:
                    # Match each FPL fixture to betting odds
                    for f in fixtures:
                        home_team = team_names.get(f.team_h, "???")
                        away_team = team_names.get(f.team_a, "???")
                        odds = betting_odds_client.get_fixture_odds(home_team, away_team, all_odds_data)
                        if odds:
                            fixture_odds_cache[f.team_h] = {**odds, "is_home": True}
                            fixture_odds_cache[f.team_a] = {**odds, "is_home": False}
            except Exception as e:
                logger.warning(f"Error fetching odds for transfer suggestions: {e}")
                fixture_odds_cache = {}

        # Team trend/reversal signals (for "trend reversal" style thinking)
        try:
            all_fixtures = fpl_client.get_fixtures(gameweek=None)
            team_trends = compute_team_trends(teams, all_fixtures, window=6, previous_window=6)
        except Exception:
            team_trends = {}
        
        # Get squad player IDs
        squad_ids = {p.id for p in request.squad}
        squad_by_pos = {"GK": [], "DEF": [], "MID": [], "FWD": []}
        for p in request.squad:
            squad_by_pos[p.position].append(p)

        # Basic validation (doesn't block, but helps the UI explain odd results)
        warnings: List[str] = []
        if len(request.squad) != len(squad_ids):
            warnings.append("Duplicate player(s) detected in squad input.")
        if len(request.squad) not in (11, 12, 13, 14, 15):
            warnings.append("Squad size looks unusual. FPL squads are 15 players (or at least 11 to get suggestions).")
        pos_counts = {k: len(v) for k, v in squad_by_pos.items()}
        if any(k not in ("GK", "DEF", "MID", "FWD") for k in pos_counts.keys()):
            warnings.append("Unknown position detected in squad input.")
        # Expected full squad composition (FYI)
        if len(request.squad) == 15 and pos_counts != {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}:
            warnings.append(f"Full squad composition is unusual (expected 2/5/5/3, got {pos_counts}).")

        # Current club counts (used to enforce max 3 per club on suggested IN moves)
        current_team_counts: Dict[int, int] = {}
        missing_ids: List[int] = []
        for sp in request.squad:
            pl = players_by_id.get(sp.id)
            if not pl:
                missing_ids.append(sp.id)
                continue
            current_team_counts[pl.team] = current_team_counts.get(pl.team, 0) + 1
        if missing_ids:
            warnings.append(f"{len(missing_ids)} squad player(s) not found in current FPL data. Suggestions may be incomplete.")
        invalid_clubs = [team_names.get(tid, str(tid)) for tid, c in current_team_counts.items() if c > 3]
        if invalid_clubs:
            warnings.append(f"Squad violates max 3 players per club for: {', '.join(invalid_clubs)}")
        
        # Analyze each player in squad - find worst performers
        squad_analysis = []
        for squad_player in request.squad:
            player = players_by_id.get(squad_player.id)
            if not player:
                continue
            
            team_name = team_names.get(player.team, "???")
            fix = fixture_info.get(player.team, {})
            rotation = assess_rotation_risk(team_name, gw_deadline, fix.get("difficulty", 3))
            trend = team_trends.get(player.team)
            reversal = trend.reversal_score if trend else 0.0
            
            try:
                features = feature_eng.extract_features(player.id, include_history=False)
                pred = predictor_heuristic.predict_player(features)
            except (ValueError, KeyError, AttributeError) as e:
                logger.debug(f"Error predicting for player {player.id}, using form: {e}")
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

            # Small boost to "keep" if the team is in a bounce-back spot (avoid rage-selling good teams at the bottom)
            if reversal >= 1.2:
                keep_score += 0.4
            
            # Penalize poor form
            if float(player.form) < 3.0:
                keep_score -= 1.0
            
            # Penalize injury doubts
            if player.status == "d":
                keep_score -= 1.5
            elif player.status in ["i", "s", "u", "n"]:
                keep_score -= 5.0
            
            squad_analysis.append({
                "id": player.id,
                "name": player.web_name,
                "team": team_name,
                "team_id": player.team,
                "position": squad_player.position,
                "price": squad_player.price,
                "predicted": round(pred, 2),
                "form": float(player.form),
                "keep_score": round(keep_score, 2),
                "fixture": fix.get("opponent", "???"),
                "fixture_difficulty": fix.get("difficulty", 3),
                "avg_fixture_5gw": round(avg_diff, 2),
                "rotation_risk": rotation.risk_level,
                "european_comp": rotation.competition,
                "status": player.status,
            })
        
        # Sort by keep_score - worst players first (transfer out candidates)
        squad_analysis.sort(key=lambda x: x["keep_score"])
        transfer_out_candidates = squad_analysis[: min(10, len(squad_analysis))]  # Top 10 worst
        
        # Find replacements for each position
        transfer_suggestions = []

        suggestions_limit = max(1, min(20, int(getattr(request, "suggestions_limit", 3) or 3)))
        # How many replacement options per OUT candidate to consider as distinct suggestions
        per_out_replacements = 3
        
        for out_player in transfer_out_candidates:
            pos = out_player["position"]
            max_price = out_player["price"] + request.bank

            # Enforce "max 3 from a club" on the resulting squad after OUT -> IN
            # Simulate removing the OUT player from its club count.
            counts_after_out = dict(current_team_counts)
            out_team_id = out_player.get("team_id")
            if isinstance(out_team_id, int):
                counts_after_out[out_team_id] = max(0, counts_after_out.get(out_team_id, 0) - 1)
            
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
                
                # Skip unavailable players (injured/suspended/not available) but allow doubtful
                if player.status in ["i", "s", "u", "n"]:
                    continue
                # Also filter by chance_of_playing_next_round - if it's None or < 50%, exclude
                chance = player.chance_of_playing_next_round
                if chance is not None and chance < 50:
                    continue
                # Check news field for injury/suspension keywords
                news_lower = (player.news or "").lower()
                if any(keyword in news_lower for keyword in ["injured", "injury", "suspended", "unavailable", "ruled out", "will miss", "out for"]):
                    continue
                
                # Allow players with at least 1 minute (includes new signings, rotation players)
                if player.minutes < 1:
                    continue

                # FPL RULE: max 3 players per club after the transfer
                if counts_after_out.get(player.team, 0) >= 3:
                    continue
                
                team_name = team_names.get(player.team, "???")
                fix = fixture_info.get(player.team, {})
                rotation = assess_rotation_risk(team_name, gw_deadline, fix.get("difficulty", 3))
                avg_diff = avg_fixture_difficulty.get(player.team, 3.0)
                trend = team_trends.get(player.team)
                reversal = trend.reversal_score if trend else 0.0
                
                try:
                    features = feature_eng.extract_features(player.id, include_history=False)
                    pred = predictor_heuristic.predict_player(features)
                except (ValueError, KeyError, AttributeError) as e:
                    logger.debug(f"Error predicting for player {player.id}, using form: {e}")
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

                # Bounce-back bonus
                if reversal >= 1.2:
                    buy_score += 0.6
                
                # Phase 2: Add betting odds bonus to transfer suggestions
                odds_data = fixture_odds_cache.get(player.team, {})
                if odds_data and betting_odds_client.enabled:
                    odds_weight = betting_odds_client.weight
                    is_home = fix.get("is_home", True)
                    
                    if player.element_type in [3, 4]:  # MID/FWD
                        # Goalscorer probability
                        games_played = max(1, player.minutes / 90.0) if player.minutes > 0 else 1
                        goals_per_game = player.goals_scored / games_played
                        xg_per_game = float(player.expected_goals) / games_played
                        
                        player_stats = {
                            "goals_per_game": goals_per_game,
                            "xg_per_game": xg_per_game,
                            "position": player.element_type,
                            "is_premium": player.price >= 9.0
                        }
                        
                        goalscorer_prob = betting_odds_client.get_player_goalscorer_odds(
                            player.web_name, odds_data, player_stats
                        )
                        if goalscorer_prob > 0:
                            buy_score += goalscorer_prob * 2.5 * odds_weight
                    
                    elif player.element_type in [1, 2]:  # GK/DEF
                        # Clean sheet probability
                        cs_prob = betting_odds_client.get_clean_sheet_probability(is_home, odds_data)
                        if cs_prob > 0:
                            buy_score += cs_prob * 2.0 * odds_weight
                    
                    # Team win bonus
                    team_win_prob = odds_data.get("home_win_prob" if is_home else "away_win_prob", 0.5)
                    win_bonus = (team_win_prob - 0.5) * 0.4 * odds_weight
                    buy_score += win_bonus
                
                replacements.append({
                    "id": player.id,
                    "name": player.web_name,
                    "team": team_name,
                    "team_id": player.team,
                    "position": pos,
                    "price": player.price,
                    "minutes": player.minutes,
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
                # Create multiple suggestions per OUT candidate for variety
                for chosen in replacements[: min(per_out_replacements, len(replacements))]:
                    points_gain = chosen["predicted"] - out_player["predicted"]

                    # Compare to ALL viable same-team same-position options (within constraints)
                    teammate_comparison = None
                    try:
                        risk_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
                        same_team = [
                            r for r in replacements
                            if r.get("team_id") == chosen.get("team_id") and r.get("id") != chosen.get("id")
                        ]
                        same_team.sort(key=lambda x: x.get("buy_score", 0), reverse=True)

                        # Rank within same team options (chosen + same_team)
                        combined = [chosen] + same_team
                        combined.sort(key=lambda x: x.get("buy_score", 0), reverse=True)
                        rank = next((i for i, x in enumerate(combined) if x.get("id") == chosen.get("id")), 0) + 1
                        total = len(combined)

                        top_alts = same_team[:12]  # send a meaningful list, avoid huge payloads
                        alt_names = [a.get("name") for a in top_alts if a.get("name")]

                        why_bits = [f"ranked #{rank} among {total} viable {chosen.get('team')} {pos} options"]
                        if same_team:
                            why_bits.append("ahead of: " + ", ".join(alt_names[:6]) + ("…" if len(alt_names) > 6 else ""))

                        # Add concrete differentiators vs best alternative (if exists)
                        if same_team:
                            top_alt = same_team[0]
                            if (chosen.get("predicted", 0) - top_alt.get("predicted", 0)) >= 0.3:
                                why_bits.append(f"higher predicted ({chosen['predicted']} vs {top_alt['predicted']})")
                            if (chosen.get("form", 0) - top_alt.get("form", 0)) >= 0.6:
                                why_bits.append(f"better form ({chosen['form']} vs {top_alt['form']})")
                            if (chosen.get("price", 0) - top_alt.get("price", 0)) <= -0.2:
                                why_bits.append(f"cheaper (£{chosen['price']}m vs £{top_alt['price']}m)")
                            if (chosen.get("minutes", 0) - top_alt.get("minutes", 0)) >= 180:
                                why_bits.append("more nailed minutes")
                            cr = risk_rank.get(chosen.get("rotation_risk", "none"), 99)
                            ar = risk_rank.get(top_alt.get("rotation_risk", "none"), 99)
                            if cr < ar:
                                why_bits.append("lower rotation risk")

                        teammate_comparison = {
                            "team": chosen.get("team"),
                            "team_id": chosen.get("team_id"),
                            "position": pos,
                            "rank": rank,
                            "total": total,
                            "why": "; ".join(why_bits) + ".",
                            "chosen": {
                                "id": chosen.get("id"),
                                "name": chosen.get("name"),
                                "price": chosen.get("price"),
                                "predicted": chosen.get("predicted"),
                                "form": chosen.get("form"),
                                "minutes": chosen.get("minutes"),
                                "rotation_risk": chosen.get("rotation_risk"),
                                "european_comp": chosen.get("european_comp"),
                                "buy_score": chosen.get("buy_score"),
                            },
                            "alternatives_total": max(0, total - 1),
                            "alternatives": [
                                {
                                    "id": a.get("id"),
                                    "name": a.get("name"),
                                    "price": a.get("price"),
                                    "predicted": a.get("predicted"),
                                    "form": a.get("form"),
                                    "minutes": a.get("minutes"),
                                    "rotation_risk": a.get("rotation_risk"),
                                    "european_comp": a.get("european_comp"),
                                    "buy_score": a.get("buy_score"),
                                }
                                for a in top_alts
                            ],
                        }
                    except Exception:
                        teammate_comparison = None

                    # Generate reason
                    reasons = []
                    if out_player["fixture_difficulty"] >= 4 and chosen["fixture_difficulty"] <= 2:
                        reasons.append(f"Fixture swing: {out_player['fixture']} (FDR {out_player['fixture_difficulty']}) → {chosen['fixture']} (FDR {chosen['fixture_difficulty']})")
                    if out_player["avg_fixture_5gw"] > chosen["avg_fixture_5gw"] + 0.5:
                        reasons.append(f"Better long-term fixtures ({chosen['avg_fixture_5gw']} vs {out_player['avg_fixture_5gw']} avg FDR)")
                    if chosen["form"] > out_player["form"] + 2:
                        reasons.append(f"Form upgrade: {out_player['form']} → {chosen['form']}")
                    if out_player["status"] != "a":
                        reasons.append(f"{out_player['name']} is {out_player['status']} (doubtful/injured)")
                    if out_player["rotation_risk"] in ["high", "medium"] and chosen["rotation_risk"] == "none":
                        reasons.append("Avoids European rotation")
                    if not reasons:
                        reasons.append(f"+{round(points_gain, 1)} predicted points")

                    transfer_suggestions.append({
                        "out": out_player,
                        "in": chosen,
                        "cost": round(chosen["price"] - out_player["price"], 1),
                        "points_gain": round(points_gain, 2),
                        "priority_score": round(chosen["buy_score"] - out_player["keep_score"], 2),
                        "reason": reasons[0],
                        "all_reasons": reasons,
                        "teammate_comparison": teammate_comparison,
                    })
        
        # Sort by priority score and take top 3
        transfer_suggestions.sort(key=lambda x: x["priority_score"], reverse=True)

        # Consider "Hold / Save transfer" as a first-class suggestion when squad looks healthy
        # and the best move is only a marginal improvement.
        hold_suggestion = None
        if squad_analysis:
            best_move = transfer_suggestions[0] if transfer_suggestions else None
            hit_cost = 0 if request.free_transfers and request.free_transfers > 0 else 4
            best_net_gain = None
            if best_move is not None:
                best_net_gain = round(float(best_move.get("points_gain", 0)) - hit_cost, 2)

            # "Squad health": avoid recommending holds when there are clear fires.
            worst = squad_analysis[0]  # lowest keep_score
            has_fire = (
                worst.get("status") in ["i", "s", "u", "n"]
                or (worst.get("status") == "d" and worst.get("keep_score", 0) < 3.5)
                or worst.get("fixture_difficulty", 3) >= 5
            )

            # If the best move is small, and we don't have obvious fires, recommend holding.
            # Thresholds are intentionally conservative to reduce "point-chasing noise".
            should_hold = (best_move is None) or (
                (best_net_gain is not None and best_net_gain < 1.0 and not has_fire)
            )
            # Also: when it would require a -4 hit, require a stronger reason to move.
            if not should_hold and best_move is not None and hit_cost == 4 and best_net_gain is not None:
                if best_net_gain < 2.5 and not has_fire:
                    should_hold = True

            if should_hold:
                why_bits = []
                if best_move is None:
                    why_bits.append("No clear upgrades found within budget/team constraints.")
                else:
                    why_bits.append(f"Best move is only ~{best_net_gain:+.2f} points after considering a -{hit_cost} hit." if hit_cost else f"Best move is only ~{best_net_gain:+.2f} points.")
                why_bits.append("Squad looks healthy (no major injury/suspension fires).")
                why_bits.append("Saving a transfer keeps flexibility for injuries/price moves.")

                hold_suggestion = {
                    "type": "hold",
                    "hit_cost": hit_cost,
                    "best_net_gain": best_net_gain,
                    "reason": "Hold / Save transfer",
                    "why": why_bits,
                    "best_alternative": best_move,
                }

        top_transfers = transfer_suggestions[:suggestions_limit]
        if hold_suggestion is not None:
            # Put hold first, then include up to N transfer options after it
            top_suggestions = [hold_suggestion] + top_transfers
        else:
            top_suggestions = top_transfers
        
        return {
            "squad_analysis": squad_analysis,
            "suggestions": top_suggestions,
            "bank": request.bank,
            "free_transfers": request.free_transfers,
            "warnings": warnings,
        }
        
    except Exception as e:
        logger.error(f"Transfer suggestion error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Wildcard ====================

@app.post("/api/wildcard")
async def get_wildcard(request: TransferRequest):
    """
    Get coordinated multi-transfer plan for wildcard (4+ transfers).
    For wildcard, considers future fixtures (next 5 gameweeks) rather than just current fixture.
    
    Optimizes all transfers together as a cohesive unit:
    - Enforces strict formation constraints (2-5-5-3)
    - Optimizes for total points gain across all transfers
    - Considers budget across all transfers
    - Ensures team balance (max 3 per team)
    """
    try:
        if request.free_transfers < 4:
            raise HTTPException(
                status_code=400,
                detail=f"Wildcard requires 4+ free transfers, got {request.free_transfers}"
            )
        
        players = fpl_client.get_players()
        teams = fpl_client.get_teams()
        team_names = {t.id: t.short_name for t in teams}
        players_by_id = {p.id: p for p in players}
        
        next_gw = fpl_client.get_next_gameweek()
        fixtures = fpl_client.get_fixtures(gameweek=next_gw.id if next_gw else None)
        gw_deadline = next_gw.deadline_time if next_gw else datetime.now()
        
        # Build fixture info
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
            except (AttributeError, KeyError, TypeError):
                pass
        
        avg_fixture_difficulty = {}
        for team_id, diffs in long_term_fixtures.items():
            avg_fixture_difficulty[team_id] = sum(diffs) / len(diffs) if diffs else 3.0
        
        # Get squad player IDs and build current squad dict
        squad_ids = {p.id for p in request.squad}
        current_squad = []
        current_team_counts: Dict[int, int] = {}
        
        for sp in request.squad:
            pl = players_by_id.get(sp.id)
            if not pl:
                continue
            
            current_team_counts[pl.team] = current_team_counts.get(pl.team, 0) + 1
            
            # Get predictions
            try:
                features = feature_eng.extract_features(pl.id, include_history=False)
                pred = predictor_heuristic.predict_player(features)
            except (ValueError, KeyError, AttributeError):
                pred = float(pl.form) if pl.form else 2.0
            
            team_name = team_names.get(pl.team, "???")
            fix = fixture_info.get(pl.team, {})
            
            current_squad.append({
                "id": pl.id,
                "name": pl.web_name,
                "position": sp.position,
                "position_id": pl.element_type,
                "price": sp.price,
                "team": team_name,
                "team_id": pl.team,
                "predicted": round(pred, 2),
                "form": float(pl.form),
                "status": pl.status,
                "fixture": fix.get("opponent", "???"),
                "fixture_difficulty": fix.get("difficulty", 3),
            })
        
        # Build all players list with predictions
        all_players = []
        player_predictions = {}
        
        for player in players:
            if player.id in squad_ids:
                continue
            
            if player.status in ["i", "s", "u", "n"]:
                continue
            # Also filter by chance_of_playing_next_round - if it's None or < 50%, exclude
            chance = player.chance_of_playing_next_round
            if chance is not None and chance < 50:
                continue
            # Check news field for injury/suspension keywords
            news_lower = (player.news or "").lower()
            if any(keyword in news_lower for keyword in ["injured", "injury", "suspended", "unavailable", "ruled out", "will miss", "out for"]):
                continue
            
            if player.minutes < 1:
                continue
            
            try:
                features = feature_eng.extract_features(player.id, include_history=False)
                pred = predictor_heuristic.predict_player(features)
            except (ValueError, KeyError, AttributeError):
                pred = float(player.form) if player.form else 2.0
            
            player_predictions[player.id] = pred
            
            team_name = team_names.get(player.team, "???")
            fix = fixture_info.get(player.team, {})
            
            all_players.append({
                "id": player.id,
                "name": player.web_name,
                "position": {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(player.element_type, "MID"),
                "position_id": player.element_type,
                "price": player.price,
                "team": team_name,
                "team_id": player.team,
                "predicted": round(pred, 2),
                "form": float(player.form),
                "status": player.status,
                "fixture": fix.get("opponent", "???"),
                "fixture_difficulty": fix.get("difficulty", 3),
            })
        
        # Generate wildcard plan
        # Handle imports for both local dev (from repo root) and Render (from backend/)
        try:
            from backend.engine.mini_rebuild import WildcardEngine
        except ImportError:
            from engine.mini_rebuild import WildcardEngine
        
        engine = WildcardEngine()
        plan = engine.generate_plan(
            current_squad=current_squad,
            all_players=all_players,
            bank=request.bank,
            free_transfers=request.free_transfers,
            player_predictions=player_predictions,
            fixture_info=fixture_info,
            avg_fixture_5gw=avg_fixture_difficulty,
            team_counts=current_team_counts,
            team_names=team_names
        )
        
        if not plan:
            raise HTTPException(
                status_code=400,
                detail="Could not generate a valid wildcard plan. Try adjusting your squad or budget."
            )
        
        return {
            "transfers_out": plan.transfers_out,
            "transfers_in": plan.transfers_in,
            "total_points_gain": plan.total_points_gain,
            "total_cost": plan.total_cost,
            "resulting_squad": plan.resulting_squad,
            "combined_rationale": plan.combined_rationale,
            "individual_breakdowns": plan.individual_breakdowns,
            "before_total_points": plan.before_total_points,
            "after_total_points": plan.after_total_points,
            "kept_players": plan.kept_players,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Wildcard error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Search Players ====================

@app.get("/api/players/search")
async def search_players(q: str = "", position: Optional[str] = None, limit: int = 50):
    """Search players by name or team for squad input."""
    try:
        # Get players with error handling
        try:
            players = fpl_client.get_players()
        except Exception as e:
            logger.error(f"Failed to get players from FPL API: {e}")
            raise HTTPException(status_code=503, detail=f"FPL API unavailable: {str(e)}")
        
        # Get teams with error handling
        try:
            teams = fpl_client.get_teams()
        except Exception as e:
            logger.error(f"Failed to get teams from FPL API: {e}")
            raise HTTPException(status_code=503, detail=f"FPL API unavailable: {str(e)}")
        
        team_names = {t.id: t.short_name for t in teams}

        # Rotation/EU badges are based on the upcoming gameweek context.
        try:
            next_gw = fpl_client.get_next_gameweek()
            fixtures = fpl_client.get_fixtures(gameweek=next_gw.id if next_gw else None)
            gw_deadline = next_gw.deadline_time if next_gw else datetime.now()
        except Exception as e:
            logger.warning(f"Failed to get gameweek/fixtures, using defaults: {e}")
            fixtures = []
            gw_deadline = datetime.now()

        fixture_info: Dict[int, Dict[str, Any]] = {}
        for f in fixtures:
            fixture_info[f.team_h] = {"difficulty": f.team_h_difficulty, "is_home": True}
            fixture_info[f.team_a] = {"difficulty": f.team_a_difficulty, "is_home": False}

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

        results = []
        for p in filtered:
            try:
                team_short = team_names.get(p.team, "???")
                fix = fixture_info.get(p.team, {})
                difficulty = fix.get("difficulty", 3)
                try:
                    rotation = assess_rotation_risk(team_short, gw_deadline, difficulty)
                    rotation_risk = rotation.risk_level
                    european_comp = rotation.competition
                except Exception as rot_error:
                    logger.warning(f"Rotation risk assessment failed for {team_short}: {rot_error}")
                    rotation_risk = "low"
                    european_comp = None
                
                results.append({
                    "id": p.id,
                    "name": p.web_name,
                    "full_name": p.full_name,
                    "team": team_short,
                    "position": p.position,
                    "price": p.price,
                    "minutes": p.minutes,
                    "status": p.status,
                    "rotation_risk": rotation_risk,
                    "european_comp": european_comp,
                })
            except Exception as player_error:
                logger.warning(f"Error processing player {p.id}: {player_error}")
                continue  # Skip this player and continue with others

        return {"players": results}
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 503 from FPL API)
        raise
    except Exception as e:
        import traceback
        logger.error(f"Search error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.get("/api/selected-teams")
async def get_selected_teams():
    """
    Get all saved teams for all gameweeks.
    Returns daily snapshot for current/next gameweek, final team for past gameweeks.
    """
    try:
        # Get current/next gameweek
        next_gw = fpl_client.get_next_gameweek()
        current_gw_id = next_gw.id if next_gw else None
        
        # Get all final teams (30 min before deadline)
        final_teams = db_manager.get_all_selected_teams()
        
        # Build response: use daily snapshot for current gameweek, final team for past
        teams_result = []
        processed_gameweeks = set()
        
        # Process all final teams
        for team in final_teams:
            gw = team["gameweek"]
            processed_gameweeks.add(gw)
            
            # For current/next gameweek, prefer daily snapshot
            if current_gw_id and gw >= current_gw_id:
                daily_snapshot = db_manager.get_latest_daily_snapshot(gw)
                if daily_snapshot:
                    teams_result.append({
                        **daily_snapshot,
                        "type": "daily_snapshot"  # Mark as daily snapshot
                    })
                else:
                    # Fallback to final team if no daily snapshot
                    teams_result.append({
                        **team,
                        "type": "final"
                    })
            else:
                # For past gameweeks, use final team
                teams_result.append({
                    **team,
                    "type": "final"
                })
        
        # If current gameweek has no final team but might have daily snapshot
        if current_gw_id and current_gw_id not in processed_gameweeks:
            daily_snapshot = db_manager.get_latest_daily_snapshot(current_gw_id)
            if daily_snapshot:
                teams_result.append({
                    **daily_snapshot,
                    "type": "daily_snapshot"
                })
        
        # Sort by gameweek descending (newest first)
        teams_result.sort(key=lambda x: x["gameweek"], reverse=True)
        
        return {"teams": teams_result}
    except Exception as e:
        logger.error(f"Error fetching selected teams: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/selected-teams/{gameweek}")
async def get_selected_team(gameweek: int):
    """
    Get saved team for a specific gameweek.
    Returns daily snapshot for current/next gameweek, final team for past gameweeks.
    """
    try:
        # Get current/next gameweek
        next_gw = fpl_client.get_next_gameweek()
        current_gw_id = next_gw.id if next_gw else None
        
        # Determine if this is current/next or past gameweek
        is_current = current_gw_id and gameweek >= current_gw_id
        
        if is_current:
            # For current gameweek, prefer daily snapshot
            team = db_manager.get_latest_daily_snapshot(gameweek)
            if team:
                return {**team, "type": "daily_snapshot"}
            # Fallback to final team if no daily snapshot
            team = db_manager.get_selected_team(gameweek)
            if team:
                return {**team, "type": "final"}
        else:
            # For past gameweeks, use final team
            team = db_manager.get_selected_team(gameweek)
            if team:
                return {**team, "type": "final"}
        
        # Not found
        raise HTTPException(status_code=404, detail=f"No selected team found for Gameweek {gameweek}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching selected team for GW{gameweek}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/daily-snapshot/update")
async def update_daily_snapshot(background_tasks: BackgroundTasks):
    """
    Manually trigger an update of the daily snapshot for the current/next gameweek.
    This forces a refresh of FPL data and regenerates the squad with latest player status.
    Also calculates and saves Triple Captain recommendations in the background.
    """
    try:
        # Run the snapshot update in the background to avoid blocking
        background_tasks.add_task(_save_daily_snapshot_async)
        return {
            "success": True,
            "message": "Daily snapshot update started in the background. This may take a few minutes."
        }
    except Exception as e:
        logger.error(f"Error scheduling daily snapshot update: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/import-fpl-team/{team_id}")
async def import_fpl_team(team_id: int, gameweek: Optional[int] = None):
    """
    Import a team from FPL by team ID.
    
    Uses the public FPL API endpoint: /api/entry/{team_id}/event/{gameweek}/picks/
    If gameweek is not provided, tries current gameweek first, then next gameweek.
    
    Returns the squad in SquadPlayer format ready for the transfers tab.
    """
    try:
        # Get gameweek (prioritize current over next, as next might not have picks yet)
        if gameweek is None:
            current_gw = fpl_client.get_current_gameweek()
            next_gw = fpl_client.get_next_gameweek()
            
            # Always try current gameweek first (team has picks for current)
            # Next gameweek often returns 404 if team hasn't set lineup yet
            gameweek = None
            if current_gw:
                gameweek = current_gw.id
            elif next_gw:
                gameweek = next_gw.id
            else:
                raise HTTPException(status_code=400, detail="No gameweek found")
        
        # Fetch team picks from FPL API - try multiple gameweeks if needed
        # Strategy: Try current gameweek first (most reliable), then past gameweeks, then next
        picks_data = None
        picks = None
        used_gameweek = gameweek
        last_response = None
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
        }
        
        # Get current and next gameweeks for fallback
        current_gw = fpl_client.get_current_gameweek()
        next_gw = fpl_client.get_next_gameweek()
        
        # Try the requested gameweek first
        picks_url = f"{fpl_client.BASE_URL}/entry/{team_id}/event/{gameweek}/picks/"
        logger.info(f"Attempting to fetch team {team_id} for gameweek {gameweek}: {picks_url}")
        response = requests.get(picks_url, headers=headers, timeout=10)
        last_response = response
        
        if response.status_code == 200:
            picks_data = response.json()
            picks = picks_data.get("picks", [])
            if picks:
                logger.info(f"Successfully fetched {len(picks)} picks for team {team_id} in gameweek {gameweek}")
        
        # If no picks found, try current gameweek (if we tried next)
        if (not picks or not picks_data) and current_gw and current_gw.id != gameweek:
            try_gameweek = current_gw.id
            picks_url = f"{fpl_client.BASE_URL}/entry/{team_id}/event/{try_gameweek}/picks/"
            logger.info(f"Trying current gameweek {try_gameweek} instead: {picks_url}")
            response = requests.get(picks_url, headers=headers, timeout=10)
            last_response = response
            if response.status_code == 200:
                picks_data = response.json()
                picks = picks_data.get("picks", [])
                if picks:
                    used_gameweek = try_gameweek
                    logger.info(f"Successfully fetched {len(picks)} picks for team {team_id} in gameweek {try_gameweek}")
        
        # If still no picks, try past gameweeks (most reliable)
        if (not picks or not picks_data) and current_gw:
            for past_gw in [current_gw.id - 1, current_gw.id - 2, current_gw.id - 3]:
                if past_gw > 0 and past_gw != used_gameweek:
                    picks_url = f"{fpl_client.BASE_URL}/entry/{team_id}/event/{past_gw}/picks/"
                    logger.info(f"Trying past gameweek {past_gw}: {picks_url}")
                    response = requests.get(picks_url, headers=headers, timeout=10)
                    last_response = response
                    if response.status_code == 200:
                        picks_data = response.json()
                        picks = picks_data.get("picks", [])
                        if picks:
                            used_gameweek = past_gw
                            logger.info(f"Successfully fetched {len(picks)} picks for team {team_id} in gameweek {past_gw}")
                            break
        
        # Last resort: try next gameweek (often 404 if lineup not set)
        if (not picks or not picks_data) and next_gw and next_gw.id != gameweek and next_gw.id != used_gameweek:
            try_gameweek = next_gw.id
            picks_url = f"{fpl_client.BASE_URL}/entry/{team_id}/event/{try_gameweek}/picks/"
            logger.info(f"Trying next gameweek {try_gameweek} as last resort: {picks_url}")
            response = requests.get(picks_url, headers=headers, timeout=10)
            last_response = response
            if response.status_code == 200:
                picks_data = response.json()
                picks = picks_data.get("picks", [])
                if picks:
                    used_gameweek = try_gameweek
                    logger.info(f"Successfully fetched {len(picks)} picks for team {team_id} in gameweek {try_gameweek}")
        
        # Final error handling
        if last_response and last_response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Team {team_id} not found or not public. Make sure the team ID is correct and the team is set to public in FPL settings.")
        if last_response and last_response.status_code != 200:
            error_text = last_response.text[:200] if hasattr(last_response, 'text') else 'Unknown error'
            raise HTTPException(status_code=last_response.status_code, detail=f"FPL API error: {last_response.status_code} - {error_text}")
        
        if not picks:
            raise HTTPException(status_code=404, detail=f"No team data found for team {team_id}. The team may not have picks for any available gameweek, or the team might be private. Please check that the team ID is correct and the team is set to public.")
        
        # Get player and team data
        players = fpl_client.get_players()
        teams = fpl_client.get_teams()
        players_by_id = {p.id: p for p in players}
        teams_by_id = {t.id: t.short_name for t in teams}
        
        # Position mapping from FPL element_type to position string
        position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
        
        # Convert picks to SquadPlayer format
        squad = []
        for pick in picks:
            player_id = pick.get("element")
            player = players_by_id.get(player_id)
            
            if not player:
                logger.warning(f"Player {player_id} not found in FPL data, skipping")
                continue
            
            # Get position
            position = position_map.get(player.element_type, "MID")
            
            # Get selling price (picks include purchase_price, but we want current price for selling)
            # The picks API returns purchase_price, but for transfers we need selling price
            # We'll use current price as default, user can edit if needed
            selling_price = player.price
            
            squad.append({
                "id": player_id,
                "name": player.web_name,
                "position": position,
                "price": selling_price,
                "team": teams_by_id.get(player.team, "UNK"),
            })
        
        if not squad:
            raise HTTPException(status_code=404, detail="No valid players found in team")
        
        # Get bank value and team name from entry data if available
        entry_url = f"{fpl_client.BASE_URL}/entry/{team_id}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
        }
        entry_response = requests.get(entry_url, headers=headers, timeout=10)
        bank = 0.0
        team_name = f"FPL Team {team_id}"
        if entry_response.status_code == 200:
            entry_data = entry_response.json()
            # Bank is in 0.1m units, convert to millions
            bank = (entry_data.get("last_deadline_bank", 0) or 0) / 10.0
            team_name = entry_data.get("name", team_name)
        
        return {
            "squad": squad,
            "bank": bank,
            "team_id": team_id,
            "gameweek": used_gameweek,
            "team_name": team_name
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing FPL team {team_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to import team: {str(e)}")


@app.post("/api/selected-teams")
async def save_selected_team():
    """
    Save the current suggested squad for the next gameweek.
    Called by scheduled job 30 minutes before deadline.
    """
    try:
        next_gw = fpl_client.get_next_gameweek()
        if not next_gw:
            raise HTTPException(status_code=400, detail="No next gameweek found")
        
        # Check if already saved
        existing = db_manager.get_selected_team(next_gw.id)
        if existing:
            return {"success": True, "gameweek": next_gw.id, "message": f"Already saved for Gameweek {next_gw.id}"}
        
        # Get the current combined squad suggestion by calling the existing endpoint logic
        # We'll reuse the get_suggested_squad logic
        squad_data = await get_suggested_squad(budget=100.0, method="combined")
        
        # Save to database
        success = db_manager.save_selected_team(next_gw.id, squad_data)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save selected team")
        
        logger.info(f"Saved selected team for Gameweek {next_gw.id} (30 min before deadline)")
        return {"success": True, "gameweek": next_gw.id, "message": f"Saved selected team for Gameweek {next_gw.id}"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving selected team: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Saved Squads (User-saved with custom names) ====================

class SaveSquadRequest(BaseModel):
    """Request model for saving a squad."""
    name: str
    squad: Dict[str, Any]  # Full squad data (formation, starting_xi, bench, captain, etc.)
    
    class Config:
        """Pydantic config."""
        json_schema_extra = {
            "example": {
                "name": "My Favorite Squad",
                "squad": {
                    "formation": "4-4-2",
                    "starting_xi": [],
                    "bench": [],
                    "captain": 123,
                    "vice_captain": 456
                }
            }
        }


@app.get("/api/saved-squads", response_model=SavedSquadsResponse)
async def get_saved_squads():
    """
    Get all user-saved squads (with custom names).
    Returns list of all saved squads sorted by most recently updated first.
    """
    try:
        squads = db_manager.get_all_saved_squads()
        return {"squads": squads}
    except Exception as e:
        logger.error(f"Error fetching saved squads: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/saved-squads/{name}", response_model=SavedSquadResponse)
async def get_saved_squad(name: str):
    """
    Get a specific saved squad by name.
    """
    try:
        squad = db_manager.get_saved_squad(name)
        if not squad:
            raise HTTPException(status_code=404, detail=f"Saved squad '{name}' not found")
        return squad
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching saved squad '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/saved-squads", response_model=SaveSquadResponse)
async def save_squad(request: SaveSquadRequest):
    """
    Save or update a squad with a custom name.
    If a squad with the same name exists, it will be updated.
    """
    try:
        name = request.name.strip() if request.name else ""
        
        # Validate squad name
        if not name:
            raise HTTPException(status_code=400, detail="Squad name is required")
        if len(name) > 200:  # Match database column limit
            raise HTTPException(status_code=400, detail="Squad name too long (max 200 characters)")
        if len(name) < 1:
            raise HTTPException(status_code=400, detail="Squad name too short")
        # Prevent XSS attempts (SQL injection is handled by SQLAlchemy)
        # Allow apostrophes and quotes in names (common in squad names)
        # Only block HTML/script tags
        if any(char in name for char in ['<', '>', '&']):
            raise HTTPException(status_code=400, detail="Squad name contains invalid characters")
        
        if not request.squad:
            raise HTTPException(status_code=400, detail="Squad data is required")
        
        success = db_manager.save_saved_squad(name, request.squad)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save squad")
        
        return {
            "success": True,
            "name": name,
            "message": f"Squad '{name}' saved successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving squad '{request.name if hasattr(request, 'name') else 'unknown'}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/saved-squads/{name}", response_model=SaveSquadResponse)
async def update_saved_squad(name: str, request: SaveSquadRequest):
    """
    Update an existing saved squad.
    The name in the URL must match the name in the request body.
    """
    try:
        if request.name != name:
            raise HTTPException(status_code=400, detail="Name in URL must match name in request body")
        
        if not request.squad:
            raise HTTPException(status_code=400, detail="Squad data is required")
        
        # Check if exists
        existing = db_manager.get_saved_squad(name)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Saved squad '{name}' not found")
        
        success = db_manager.save_saved_squad(name, request.squad)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update squad")
        
        return {
            "success": True,
            "name": name,
            "message": f"Squad '{name}' updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating squad '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/saved-squads/{name}", response_model=DeleteSquadResponse)
async def delete_saved_squad(name: str):
    """
    Delete a saved squad by name.
    """
    try:
        success = db_manager.delete_saved_squad(name)
        if not success:
            raise HTTPException(status_code=404, detail=f"Saved squad '{name}' not found")
        
        return {
            "success": True,
            "name": name,
            "message": f"Squad '{name}' deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting squad '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== FPL Teams API ====================

@app.get("/api/fpl-teams", response_model=FplTeamsResponse)
async def get_fpl_teams():
    """
    Get all saved FPL team IDs.
    Returns list of all saved FPL teams sorted by most recently imported first.
    """
    try:
        teams = db_manager.get_all_fpl_teams()
        return {"teams": teams}
    except Exception as e:
        logger.error(f"Error fetching FPL teams: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SaveFplTeamRequest(BaseModel):
    """Request model for saving an FPL team."""
    team_id: int
    team_name: str
    
    class Config:
        """Pydantic config."""
        json_schema_extra = {
            "example": {
                "team_id": 12345,
                "team_name": "My FPL Team"
            }
        }


@app.post("/api/fpl-teams", response_model=SaveFplTeamResponse)
async def save_fpl_team(request: SaveFplTeamRequest):
    """
    Save or update an FPL team ID.
    If a team with the same ID exists, it will be updated.
    """
    try:
        team_id = request.team_id
        team_name = request.team_name.strip() if request.team_name else ""
        
        if not team_id or team_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid team ID")
        if not team_name:
            raise HTTPException(status_code=400, detail="Team name is required")
        if len(team_name) > 200:
            raise HTTPException(status_code=400, detail="Team name too long (max 200 characters)")
        
        success = db_manager.save_fpl_team(team_id, team_name)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save FPL team")
        
        return {
            "success": True,
            "teamId": team_id,
            "teamName": team_name,
            "message": f"FPL team ID {team_id} saved successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving FPL team: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Tasks API ====================

@app.get("/api/tasks")
async def get_tasks(include_old: bool = False):
    """
    Get all tasks.
    
    Args:
        include_old: If True, include old completed tasks. If False, only return tasks from last 5 minutes or running/pending tasks.
    
    Returns:
        List of tasks
    """
    try:
        tasks = db_manager.get_all_tasks(include_old=include_old)
        return {"tasks": tasks}
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """
    Get a specific task by ID.
    
    Args:
        task_id: Unique task identifier
    
    Returns:
        Task data
    """
    try:
        task = db_manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tasks")
async def create_task(request: dict):
    """
    Create a new task.
    
    Request body:
        - task_id: Unique task identifier (required)
        - task_type: Type of task (required)
        - title: Task title (required)
        - description: Task description (optional)
        - status: Task status (default: "pending")
        - progress: Progress percentage 0-100 (default: 0)
    
    Returns:
        Created task data
    """
    try:
        task_id = request.get("task_id") or request.get("id")
        if not task_id:
            raise HTTPException(status_code=400, detail="task_id is required")
        
        task_type = request.get("task_type") or request.get("type")
        if not task_type:
            raise HTTPException(status_code=400, detail="task_type is required")
        
        title = request.get("title")
        if not title:
            raise HTTPException(status_code=400, detail="title is required")
        
        task = db_manager.create_task(
            task_id=task_id,
            task_type=task_type,
            title=title,
            description=request.get("description"),
            status=request.get("status", "pending"),
            progress=request.get("progress", 0)
        )
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/tasks/{task_id}")
async def update_task(task_id: str, request: dict):
    """
    Update an existing task.
    
    Request body (all optional):
        - status: New status
        - progress: New progress (0-100)
        - error: Error message if failed
    
    Returns:
        Updated task data
    """
    try:
        task = db_manager.update_task(
            task_id=task_id,
            status=request.get("status"),
            progress=request.get("progress"),
            error=request.get("error")
        )
        if not task:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """
    Delete a task.
    
    Args:
        task_id: Unique task identifier
    
    Returns:
        Success message
    """
    try:
        deleted = db_manager.delete_task(task_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        return {"success": True, "message": f"Task '{task_id}' deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Wake-up Endpoint for Render Free Tier ====================

@app.post("/api/wake-up", response_model=HealthResponse)
async def wake_up():
    """
    Endpoint to wake up the server and check for missed saves.
    Can be called by external cron services (cron-job.org, etc.) to keep the server alive
    and trigger missed saves when the server wakes up.
    
    Use this with a cron service to ping every 30-60 minutes to:
    1. Keep the server from spinning down
    2. Trigger any missed saves if the server was asleep
    """
    try:
        # Run missed save checks
        await check_and_run_missed_saves()
        
        # Also trigger a reschedule to ensure jobs are properly scheduled
        schedule_next_save()
        
        return {
            "status": "awake",
            "message": "Server is awake and checked for missed saves",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error in wake-up endpoint: {e}")
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
