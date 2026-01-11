"""
Suggested squad endpoint.

Route for getting AI-optimized squad suggestions.
"""

import logging
from fastapi import APIRouter, HTTPException

from services.dependencies import get_dependencies
from services.squad_service import build_squad_with_predictor

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/suggested-squad")
async def get_suggested_squad(budget: float = 100.0, method: str = "combined", refresh: bool = False):
    """
    Get optimal suggested squad for next gameweek.
    
    Args:
        budget: Total budget in millions (default 100.0)
        method: Prediction method - "heuristic", "form", "fixture", or "combined" (default)
        refresh: Force refresh FPL data cache (default False)
    """
    try:
        deps = get_dependencies()
        
        # Select predictor based on method
        if method == "form":
            predictor = deps.predictor_form
            method_name = "form"
        elif method == "fixture":
            predictor = deps.predictor_fixture
            method_name = "fixture"
        elif method == "heuristic":
            predictor = deps.predictor_heuristic
            method_name = "heuristic"
        else:
            # Default: combined (using heuristic which already combines multiple factors)
            predictor = deps.predictor_heuristic
            method_name = "combined"
        
        result = await build_squad_with_predictor(
            predictor, 
            method_name, 
            budget=budget,
            force_refresh=refresh
        )
        
        return {"squad": result}
        
    except Exception as e:
        logger.error(f"Squad suggestion error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

