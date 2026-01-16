"""
Chip optimization API endpoints.

Provides endpoints for Triple Captain, Bench Boost, and Wildcard chip optimization.
"""

import logging
import uuid
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from fpl.client import FPLClient
from ml.features import FeatureEngineer
from ml.predictor import HeuristicPredictor
from ml.chips import TripleCaptainOptimizer, WildcardOptimizer
from services.dependencies import get_dependencies
from services.cache import cache

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize components (will be set by main.py)
fpl_client: Optional[FPLClient] = None
feature_engineer: Optional[FeatureEngineer] = None
predictor: Optional[HeuristicPredictor] = None


def initialize_chips_router(client: FPLClient, engineer: FeatureEngineer, pred: Optional[HeuristicPredictor] = None):
    """Initialize the chips router with dependencies."""
    global fpl_client, feature_engineer, predictor
    fpl_client = client
    feature_engineer = engineer
    predictor = pred or HeuristicPredictor()


@router.get("/triple-captain")
async def get_triple_captain_recommendations(
    gameweek: Optional[int] = Query(None, description="Specific gameweek to get recommendations for. If not provided, returns all recommendations."),
    top_n: int = Query(20, ge=1, le=50, description="Number of top recommendations to return per gameweek")
):
    """
    Get Triple Captain recommendations from database (calculated daily at midnight).
    
    If gameweek is provided, returns recommendations for that specific gameweek.
    If not provided, returns all recommendations for all gameweeks (for tab display).
    
    Returns cached recommendations that were calculated during the daily snapshot job.
    """
    from database.crud import DatabaseManager
    
    if not fpl_client:
        raise HTTPException(
            status_code=500,
            detail="Chips router not initialized. Please ensure dependencies are set."
        )
    
    try:
        db_manager = DatabaseManager()
        
        if gameweek is not None:
            # Get recommendations for specific gameweek
            cached_recs = db_manager.get_triple_captain_recommendations(gameweek)
            
            if cached_recs:
                recommendations = cached_recs["recommendations"][:top_n]
                return {
                    "recommendations": recommendations,
                    "gameweek_range": cached_recs["gameweek_range"],
                    "total_recommendations": len(recommendations),
                    "calculated_at": cached_recs["calculated_at"],
                    "gameweek": cached_recs["gameweek"],
                    "cached": True
                }
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"No Triple Captain recommendations found for GW{gameweek}."
                )
        else:
            # Get all recommendations for all gameweeks
            all_recs = db_manager.get_all_triple_captain_recommendations()
            
            if not all_recs:
                raise HTTPException(
                    status_code=404,
                    detail="No Triple Captain recommendations found. They will be calculated at midnight."
                )
            
            # Format response similar to Free Hit teams structure
            return {
                "recommendations_by_gameweek": {
                    rec["gameweek"]: {
                        "gameweek": rec["gameweek"],
                        "recommendations": rec["recommendations"][:top_n],
                        "gameweek_range": rec["gameweek_range"],
                        "total_recommendations": len(rec["recommendations"]),
                        "calculated_at": rec["calculated_at"]
                    }
                    for rec in all_recs
                }
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting Triple Captain recommendations: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get Triple Captain recommendations: {str(e)}"
        )


def _calculate_triple_captain_background(gameweek_id: int):
    """
    Background task to calculate Triple Captain recommendations.
    This runs asynchronously so the API can return immediately.
    """
    try:
        from database.crud import DatabaseManager
        
        logger.info(f"Starting background calculation of Triple Captain recommendations for GW{gameweek_id}")
        optimizer = TripleCaptainOptimizer(fpl_client, feature_engineer)
        recommendations = optimizer.get_triple_captain_recommendations(
            gameweek_range=5,
            top_n=20
        )
        
        # Save to database
        db_manager = DatabaseManager()
        success = db_manager.save_triple_captain_recommendations(
            gameweek=gameweek_id,
            recommendations=recommendations,
            gameweek_range=5
        )
        
        if success:
            logger.info(f"Successfully calculated and saved {len(recommendations)} Triple Captain recommendations for GW{gameweek_id}")
        else:
            logger.error(f"Failed to save Triple Captain recommendations for GW{gameweek_id} to database")
    except Exception as e:
        logger.error(f"Error in background Triple Captain calculation for GW{gameweek_id}: {e}", exc_info=True)


@router.post("/triple-captain/calculate")
async def calculate_triple_captain_recommendations(background_tasks: BackgroundTasks):
    """
    Manually trigger calculation of Triple Captain recommendations.
    This will calculate and save recommendations to the database in the background.
    Returns immediately - calculation runs asynchronously.
    Usually done automatically at midnight with the daily snapshot.
    """
    if not fpl_client or not feature_engineer:
        raise HTTPException(
            status_code=500,
            detail="Chips router not initialized. Please ensure dependencies are set."
        )
    
    try:
        # Get current/next gameweek
        next_gw = fpl_client.get_next_gameweek()
        if not next_gw:
            raise HTTPException(
                status_code=404,
                detail="No next gameweek found"
            )
        
        # Add calculation to background tasks
        background_tasks.add_task(_calculate_triple_captain_background, next_gw.id)
        
        logger.info(f"Queued Triple Captain calculation for GW{next_gw.id} (running in background)")
        
        return {
            "success": True,
            "message": f"Triple Captain calculation started for GW{next_gw.id}. Results will be available shortly.",
            "gameweek": next_gw.id,
            "status": "processing"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queuing Triple Captain calculation: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue Triple Captain calculation: {str(e)}"
        )


@router.post("/bench-boost")
async def get_bench_boost_squad(
    budget: float = Query(100.0, ge=0.0, description="Budget constraint"),
    gameweek_range: int = Query(3, ge=1, le=5, description="Number of gameweeks to optimize for")
):
    """
    Get optimized Bench Boost squad.
    
    Optimizes a 15-man squad (not just starting XI) using MILP over multiple gameweeks.
    
    Returns:
        Optimized 15-man squad with starting XI and bench
    """
    # TODO: Implement Bench Boost optimization
    raise HTTPException(
        status_code=501,
        detail="Bench Boost optimization not yet implemented"
    )


class WildcardRequest(BaseModel):
    """Request model for Wildcard optimization."""
    current_squad: Optional[List[Dict[str, Any]]] = None
    budget: float = 100.0
    horizon: int = 8


def _calculate_wildcard_background(task_id: str, budget: float, horizon: int, current_squad: Optional[List[Dict]]):
    """
    Background task to calculate Wildcard trajectory.
    This runs asynchronously so the API can return immediately.
    """
    logger.info(f"Wildcard trajectory background calculation started (task_id={task_id}, budget={budget}, horizon={horizon})")
    try:
        deps = get_dependencies()
        db_manager = deps.db_manager
        
        # Update task to running
        db_manager.update_task(task_id, status="running", progress=10)
        
        # Get dependencies for optimizer
        # Use global variables if available, otherwise get from deps
        opt_fpl_client = fpl_client or deps.fpl_client
        opt_feature_engineer = feature_engineer or deps.feature_engineer
        opt_predictor = predictor or deps.predictor_heuristic
        
        optimizer = WildcardOptimizer(opt_fpl_client, opt_feature_engineer, opt_predictor)
        
        # Update progress
        db_manager.update_task(task_id, progress=30)
        
        trajectory = optimizer.get_optimal_trajectory(
            budget=budget,
            horizon=horizon,
            current_squad=current_squad
        )
        
        if not trajectory:
            db_manager.update_task(
                task_id,
                status="failed",
                error="Failed to generate wildcard trajectory. Please try again."
            )
            return
        
        # Update progress
        db_manager.update_task(task_id, progress=80)
        
        # Convert to dict
        trajectory_dict = optimizer.trajectory_to_dict(trajectory)
        
        # Save to database (replaces any existing)
        save_success = db_manager.save_wildcard_trajectory(trajectory_dict, budget, horizon)
        if not save_success:
            logger.error(f"Failed to save wildcard trajectory to database (task_id={task_id})")
            db_manager.update_task(
                task_id,
                status="failed",
                error="Failed to save wildcard trajectory to database"
            )
            return
        
        logger.info(f"Wildcard trajectory saved to database successfully (task_id={task_id}, budget={budget}, horizon={horizon})")
        
        # Also store in cache for immediate retrieval
        cache.set("wildcard_results", task_id, trajectory_dict)
        
        # Mark task as completed
        db_manager.update_task(task_id, status="completed", progress=100)
        logger.info(f"Wildcard trajectory task {task_id} completed successfully and is available via /api/chips/wildcard-trajectory/latest")
        
    except Exception as e:
        logger.error(f"Error in background Wildcard calculation for task {task_id}: {e}", exc_info=True)
        try:
            deps = get_dependencies()
            deps.db_manager.update_task(
                task_id,
                status="failed",
                error=str(e)
            )
        except Exception:
            pass


@router.post("/wildcard-trajectory")
async def get_wildcard_trajectory(request: WildcardRequest, background_tasks: BackgroundTasks):
    """
    Get optimized 8-GW Wildcard trajectory (async task-based).
    
    Creates a background task and returns immediately with a task ID.
    Use GET /api/tasks/{task_id} to check status and GET /api/chips/wildcard-trajectory/{task_id} to get results.
    
    Uses hybrid LSTM+XGBoost model with:
    - Weighted formula: 0.7×LSTM + 0.3×XGBoost
    - Fixture Difficulty Rating (FDR) adjustment
    - Transfer decay factor for uncertainty over time
    - MILP optimizer for optimal squad path
    
    Prioritizes long-term fixture blocks over single-week peaks.
    
    Args:
        request: WildcardRequest with budget, horizon, and optional current_squad
        background_tasks: FastAPI background tasks
        
    Returns:
        Task ID and status
    """
    if not fpl_client or not feature_engineer:
        raise HTTPException(
            status_code=500,
            detail="Chips router not initialized. Please ensure dependencies are set."
        )
    
    try:
        deps = get_dependencies()
        db_manager = deps.db_manager
        
        # Check if there's already a running or pending wildcard task
        all_tasks = db_manager.get_all_tasks(include_old=False)
        existing_wildcard = next(
            (task for task in all_tasks if task.get("type") == "wildcard" and task.get("status") in ["pending", "running"]),
            None
        )
        
        if existing_wildcard:
            raise HTTPException(
                status_code=409,
                detail=f"A wildcard trajectory calculation is already in progress (task: {existing_wildcard['id']}). Please wait for it to complete."
            )
        
        # Generate unique task ID
        task_id = f"wildcard_{uuid.uuid4().hex[:12]}"
        
        # Create task
        db_manager.create_task(
            task_id=task_id,
            task_type="wildcard",
            title="Generate Wildcard Trajectory",
            description=f"Optimizing {request.horizon}-gameweek wildcard trajectory with budget £{request.budget}m",
            status="pending",
            progress=0
        )
        
        # Add to background tasks
        background_tasks.add_task(
            _calculate_wildcard_background,
            task_id,
            request.budget,
            request.horizon,
            request.current_squad
        )
        
        logger.info(f"Queued Wildcard trajectory calculation (task {task_id})")
        
        return {
            "task_id": task_id,
            "status": "pending",
            "message": "Wildcard trajectory calculation started. Check task status for progress."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queuing Wildcard trajectory calculation: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue Wildcard trajectory calculation: {str(e)}"
        )


@router.get("/wildcard-trajectory/latest")
async def get_latest_wildcard_trajectory():
    """
    Get the latest saved wildcard trajectory from database.
    
    Returns:
        Latest wildcard trajectory or 404 if not found
    """
    try:
        deps = get_dependencies()
        db_manager = deps.db_manager
        
        logger.info("Fetching latest wildcard trajectory from database...")
        trajectory = db_manager.get_wildcard_trajectory()
        
        if not trajectory:
            logger.warning("No wildcard trajectory found in database")
            raise HTTPException(
                status_code=404,
                detail="No wildcard trajectory found. Generate one first."
            )
        
        logger.info(f"Successfully retrieved wildcard trajectory from database (has {len(trajectory.get('squad', []))} players in squad)")
        return trajectory
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting latest wildcard trajectory: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get latest wildcard trajectory: {str(e)}"
        )


@router.get("/wildcard-trajectory/{task_id}")
async def get_wildcard_trajectory_result(task_id: str):
    """
    Get Wildcard trajectory result by task ID.
    
    Args:
        task_id: Task ID returned from POST /api/chips/wildcard-trajectory
        
    Returns:
        Wildcard trajectory result if task is completed, otherwise error
    """
    try:
        deps = get_dependencies()
        db_manager = deps.db_manager
        
        # Check task status
        task = db_manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        
        if task["status"] != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Task '{task_id}' is not completed yet. Status: {task['status']}"
            )
        
        # Get result from cache
        result = cache.get("wildcard_results", task_id)
        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"Result for task '{task_id}' not found. It may have expired."
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting Wildcard trajectory result for task {task_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get Wildcard trajectory result: {str(e)}"
        )



