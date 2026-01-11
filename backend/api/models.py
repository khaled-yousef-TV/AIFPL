"""
Shared API request/response models.

These are Pydantic models used across multiple routes.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel


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
    suggestions_limit: int = 3  # How many transfer moves to return

