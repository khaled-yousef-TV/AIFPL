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
    GameWeekResponse, SuggestedSquadResponse,
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

# Include modular routes
from api.routes import chips as chips_router
from api.routes import health as health_router
from api.routes import gameweek as gameweek_router
from api.routes import players as players_router
from api.routes import tasks as tasks_router
from api.routes import fpl_teams as fpl_teams_router
from api.routes import selected_teams as selected_teams_router
from api.routes import predictions as predictions_router
from api.routes import suggested_squad as suggested_squad_router
from api.routes import transfers as transfers_router

# Initialize dependencies for routes
from services.dependencies import init_dependencies
deps = init_dependencies()

# Initialize routers with dependencies (for legacy routers that need explicit init)
chips_router.initialize_chips_router(deps.fpl_client, deps.feature_engineer)
health_router.initialize_health_router(deps.betting_odds_client)

# Register all routers
app.include_router(chips_router.router, prefix="/api/chips", tags=["chips"])
app.include_router(health_router.router, prefix="/api", tags=["health"])
app.include_router(gameweek_router.router, prefix="/api", tags=["gameweek"])
app.include_router(players_router.router, prefix="/api/players", tags=["players"])
app.include_router(tasks_router.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(fpl_teams_router.router, prefix="/api/fpl-teams", tags=["fpl-teams"])
app.include_router(selected_teams_router.router, prefix="/api/selected-teams", tags=["selected-teams"])
app.include_router(predictions_router.router, prefix="/api", tags=["predictions"])
app.include_router(suggested_squad_router.router, prefix="/api", tags=["squad"])
app.include_router(transfers_router.router, prefix="/api", tags=["transfers"])


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
# NOTE: /api/health and /api/betting-odds-status moved to api/routes/health.py


# NOTE: /api/gameweek moved to api/routes/gameweek.py

# NOTE: /api/predictions, /api/top-picks, /api/differentials, /api/team-trends moved to api/routes/predictions.py

# NOTE: Squad building logic moved to services/squad_service.py
# NOTE: /api/suggested-squad moved to api/routes/suggested_squad.py

# NOTE: /api/transfer-suggestions moved to api/routes/transfers.py
# NOTE: Logic moved to services/transfer_service.py

# NOTE: /api/wildcard moved to api/routes/transfers.py
# NOTE: Logic moved to services/wildcard_service.py

# NOTE: /api/players/search moved to api/routes/players.py


# NOTE: /api/selected-teams GET routes moved to api/routes/selected_teams.py

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


# NOTE: /api/import-fpl-team/{team_id} moved to api/routes/fpl_teams.py
# NOTE: Logic moved to services/fpl_import_service.py

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


# NOTE: /api/fpl-teams/* moved to api/routes/fpl_teams.py

# NOTE: /api/tasks/* moved to api/routes/tasks.py

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
