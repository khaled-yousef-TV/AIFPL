"""
Chip optimization API endpoints.

Provides endpoints for Triple Captain, Bench Boost, and Wildcard chip optimization.
"""

import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from fpl.client import FPLClient
from ml.features import FeatureEngineer
from ml.predictor import HeuristicPredictor
from ml.chips import TripleCaptainOptimizer, WildcardOptimizer

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


@router.post("/wildcard-trajectory")
async def get_wildcard_trajectory(request: WildcardRequest):
    """
    Get optimized 8-GW Wildcard trajectory.
    
    Uses hybrid LSTM+XGBoost model with:
    - Weighted formula: 0.7×LSTM + 0.3×XGBoost
    - Fixture Difficulty Rating (FDR) adjustment
    - Transfer decay factor for uncertainty over time
    - MILP optimizer for optimal squad path
    
    Prioritizes long-term fixture blocks over single-week peaks.
    
    Args:
        request: WildcardRequest with budget, horizon, and optional current_squad
        
    Returns:
        Optimal squad trajectory with gameweek-by-gameweek predictions
    """
    if not fpl_client or not feature_engineer:
        raise HTTPException(
            status_code=500,
            detail="Chips router not initialized. Please ensure dependencies are set."
        )
    
    try:
        optimizer = WildcardOptimizer(fpl_client, feature_engineer, predictor)
        
        trajectory = optimizer.get_optimal_trajectory(
            budget=request.budget,
            horizon=request.horizon,
            current_squad=request.current_squad
        )
        
        if not trajectory:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate wildcard trajectory. Please try again."
            )
        
        return optimizer.trajectory_to_dict(trajectory)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating wildcard trajectory: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate wildcard trajectory: {str(e)}"
        )


@router.get("/wildcard-trajectory")
async def get_wildcard_trajectory_get(
    budget: float = Query(100.0, ge=0.0, description="Budget constraint"),
    horizon: int = Query(8, ge=1, le=10, description="Number of gameweeks to optimize")
):
    """
    Get optimized 8-GW Wildcard trajectory (GET endpoint).
    
    Same as POST but with query parameters for easier testing.
    """
    request = WildcardRequest(budget=budget, horizon=horizon)
    return await get_wildcard_trajectory(request)

