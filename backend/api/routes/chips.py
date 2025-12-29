"""
Chip optimization API endpoints.

Provides endpoints for Triple Captain, Bench Boost, and Wildcard chip optimization.
"""

import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from fpl.client import FPLClient
from ml.features import FeatureEngineer
from ml.chips import TripleCaptainOptimizer

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize components (will be set by main.py)
fpl_client: Optional[FPLClient] = None
feature_engineer: Optional[FeatureEngineer] = None


def initialize_chips_router(client: FPLClient, engineer: FeatureEngineer):
    """Initialize the chips router with dependencies."""
    global fpl_client, feature_engineer
    fpl_client = client
    feature_engineer = engineer


@router.get("/triple-captain")
async def get_triple_captain_recommendations(
    gameweek_range: int = Query(5, ge=1, le=10, description="Number of gameweeks to analyze"),
    top_n: int = Query(20, ge=1, le=50, description="Number of top recommendations to return")
):
    """
    Get Triple Captain recommendations from database (calculated daily at midnight).
    
    Returns cached recommendations that were calculated during the daily snapshot job.
    """
    from database.crud import DatabaseManager
    
    if not fpl_client:
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
        
        # Get recommendations from database
        db_manager = DatabaseManager()
        cached_recs = db_manager.get_triple_captain_recommendations(next_gw.id)
        
        if cached_recs:
            # Return cached recommendations
            recommendations = cached_recs["recommendations"][:top_n]
            return {
                "recommendations": recommendations,
                "gameweek_range": cached_recs["gameweek_range"],
                "total_recommendations": len(recommendations),
                "calculated_at": cached_recs["calculated_at"],
                "cached": True
            }
        else:
            # No cached recommendations found
            raise HTTPException(
                status_code=404,
                detail=f"No Triple Captain recommendations found for GW{next_gw.id}. They will be calculated at midnight."
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting Triple Captain recommendations: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get Triple Captain recommendations: {str(e)}"
        )


@router.post("/triple-captain/calculate")
async def calculate_triple_captain_recommendations():
    """
    Manually trigger calculation of Triple Captain recommendations.
    This will calculate and save recommendations to the database.
    Usually done automatically at midnight with the daily snapshot.
    """
    if not fpl_client or not feature_engineer:
        raise HTTPException(
            status_code=500,
            detail="Chips router not initialized. Please ensure dependencies are set."
        )
    
    try:
        from database.crud import DatabaseManager
        
        # Get current/next gameweek
        next_gw = fpl_client.get_next_gameweek()
        if not next_gw:
            raise HTTPException(
                status_code=404,
                detail="No next gameweek found"
            )
        
        # Calculate recommendations
        logger.info(f"Manually calculating Triple Captain recommendations for GW{next_gw.id}")
        optimizer = TripleCaptainOptimizer(fpl_client, feature_engineer)
        recommendations = optimizer.get_triple_captain_recommendations(
            gameweek_range=5,
            top_n=20
        )
        
        # Save to database
        db_manager = DatabaseManager()
        success = db_manager.save_triple_captain_recommendations(
            gameweek=next_gw.id,
            recommendations=recommendations,
            gameweek_range=5
        )
        
        if success:
            return {
                "success": True,
                "message": f"Triple Captain recommendations calculated and saved for GW{next_gw.id}",
                "total_recommendations": len(recommendations),
                "gameweek": next_gw.id
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to save recommendations to database"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating Triple Captain recommendations: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to calculate Triple Captain recommendations: {str(e)}"
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

@router.post("/wildcard")
async def get_wildcard_squad(request: WildcardRequest):
    """
    Get optimized Wildcard squad.
    
    Optimizes squad over 8 gameweeks using LSTM+XGBoost predictions with transfer decay.
    
    Returns:
        Optimal squad path over specified horizon
    """
    # TODO: Implement Wildcard optimization
    raise HTTPException(
        status_code=501,
        detail="Wildcard optimization not yet implemented"
    )

