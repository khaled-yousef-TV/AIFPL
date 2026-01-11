"""
Transfer suggestion endpoints.

Routes for transfer recommendations and wildcard planning.
"""

import logging
from fastapi import APIRouter, HTTPException

from api.models import TransferRequest
from services.transfer_service import get_transfer_suggestions

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/transfer-suggestions")
async def transfer_suggestions(request: TransferRequest):
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
        # Convert Pydantic models to dicts for service
        squad = [
            {"id": p.id, "name": p.name, "position": p.position, "price": p.price}
            for p in request.squad
        ]
        
        result = await get_transfer_suggestions(
            squad=squad,
            bank=request.bank,
            free_transfers=request.free_transfers,
            suggestions_limit=request.suggestions_limit
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Transfer suggestion error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

