"""
Response Models for API Endpoints

Pydantic models for API responses to improve type safety and documentation.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class GameWeekInfo(BaseModel):
    """Gameweek information."""
    id: int
    name: Optional[str] = None
    finished: Optional[bool] = None
    deadline: Optional[str] = None


class GameWeekResponse(BaseModel):
    """Response for gameweek endpoint."""
    current: Optional[GameWeekInfo] = None
    next: Optional[GameWeekInfo] = None


class PlayerResponse(BaseModel):
    """Player information in API responses."""
    id: int
    name: str
    team: str
    position: str
    price: float
    predicted: Optional[float] = None
    form: Optional[float] = None
    total_points: Optional[int] = None
    ownership: Optional[float] = None
    rotation_risk: Optional[str] = None
    european_comp: Optional[str] = None
    opponent: Optional[str] = None
    difficulty: Optional[int] = None
    is_home: Optional[bool] = None
    reason: Optional[str] = None


class CaptainInfo(BaseModel):
    """Captain information."""
    id: int
    name: str
    predicted: float


class SuggestedSquadResponse(BaseModel):
    """Response for suggested squad endpoint."""
    gameweek: int
    formation: str
    starting_xi: List[Dict[str, Any]] = Field(..., description="Starting XI players")
    bench: List[Dict[str, Any]] = Field(..., description="Bench players")
    captain: CaptainInfo
    vice_captain: CaptainInfo
    total_cost: float
    remaining_budget: float
    predicted_points: float


class SavedSquadInfo(BaseModel):
    """Saved squad information."""
    id: int
    name: str
    squad: Dict[str, Any]
    saved_at: str
    updated_at: str


class SavedSquadsResponse(BaseModel):
    """Response for saved squads list endpoint."""
    squads: List[SavedSquadInfo]


class SavedSquadResponse(BaseModel):
    """Response for single saved squad endpoint."""
    id: int
    name: str
    squad: Dict[str, Any]
    saved_at: str
    updated_at: str


class SaveSquadResponse(BaseModel):
    """Response for save/update squad endpoint."""
    success: bool
    name: str
    message: str


class DeleteSquadResponse(BaseModel):
    """Response for delete squad endpoint."""
    success: bool
    name: str
    message: str


class FplTeamInfo(BaseModel):
    """FPL team information."""
    id: int
    teamId: int
    teamName: str
    savedAt: str
    lastImported: str


class FplTeamsResponse(BaseModel):
    """Response for FPL teams list endpoint."""
    teams: List[FplTeamInfo]


class SaveFplTeamResponse(BaseModel):
    """Response for save/update FPL team endpoint."""
    success: bool
    teamId: int
    teamName: str
    message: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    timestamp: str


class BettingOddsDebugResponse(BaseModel):
    """Response for betting odds debug endpoint."""
    enabled: bool
    has_api_key: bool
    api_key_set: bool
    enabled_env: str

