"""
FPL Data Models

Pydantic models for FPL API responses.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class Player(BaseModel):
    """FPL Player model."""
    id: int
    first_name: str
    second_name: str
    web_name: str
    team: int
    team_code: int
    element_type: int  # 1=GK, 2=DEF, 3=MID, 4=FWD
    now_cost: int  # Price in 0.1m units (e.g., 100 = £10.0m)
    
    # Stats
    total_points: int = 0
    points_per_game: float = 0.0
    minutes: int = 0
    goals_scored: int = 0
    assists: int = 0
    clean_sheets: int = 0
    goals_conceded: int = 0
    bonus: int = 0
    bps: int = 0  # Bonus Points System
    
    # Form and selection
    form: float = 0.0
    selected_by_percent: float = 0.0
    transfers_in_event: int = 0
    transfers_out_event: int = 0
    
    # ICT Index
    influence: float = 0.0
    creativity: float = 0.0
    threat: float = 0.0
    ict_index: float = 0.0
    
    # Expected stats
    expected_goals: float = Field(0.0, alias="expected_goals")
    expected_assists: float = Field(0.0, alias="expected_assists")
    expected_goal_involvements: float = Field(0.0, alias="expected_goal_involvements")
    expected_goals_conceded: float = Field(0.0, alias="expected_goals_conceded")
    
    # Status
    status: str = "a"  # a=available, d=doubtful, i=injured, s=suspended, u=unavailable, n=not available (e.g. international duty)
    chance_of_playing_next_round: Optional[int] = 100
    news: str = ""
    
    @property
    def price(self) -> float:
        """Get price in millions."""
        return self.now_cost / 10
    
    @property
    def position(self) -> str:
        """Get position name."""
        positions = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
        return positions.get(self.element_type, "Unknown")
    
    @property
    def full_name(self) -> str:
        """Get full name."""
        return f"{self.first_name} {self.second_name}"
    
    class Config:
        populate_by_name = True


class Team(BaseModel):
    """FPL Team model."""
    id: int
    name: str
    short_name: str
    code: int
    strength: int
    strength_overall_home: int
    strength_overall_away: int
    strength_attack_home: int
    strength_attack_away: int
    strength_defence_home: int
    strength_defence_away: int


class Fixture(BaseModel):
    """FPL Fixture model."""
    id: int
    event: Optional[int]  # Gameweek number
    team_h: int  # Home team ID
    team_a: int  # Away team ID
    team_h_difficulty: int  # FDR for home team
    team_a_difficulty: int  # FDR for away team
    kickoff_time: Optional[datetime]
    finished: bool = False
    started: bool = False
    
    # Scores (only if started/finished)
    team_h_score: Optional[int] = None
    team_a_score: Optional[int] = None


class GameWeek(BaseModel):
    """FPL Gameweek model."""
    id: int
    name: str
    deadline_time: datetime
    is_current: bool = False
    is_next: bool = False
    is_previous: bool = False
    finished: bool = False
    
    # Stats
    average_entry_score: Optional[int] = None
    highest_score: Optional[int] = None
    highest_scoring_entry: Optional[int] = None


class MyTeamPlayer(BaseModel):
    """Player in user's team."""
    element: int  # Player ID
    position: int  # Position in team (1-15)
    is_captain: bool = False
    is_vice_captain: bool = False
    multiplier: int = 1  # 0=benched, 1=playing, 2=captain, 3=triple captain


class MyTeam(BaseModel):
    """User's FPL team."""
    picks: List[MyTeamPlayer]
    chips: List[Dict[str, Any]] = []
    transfers: Dict[str, Any] = {}
    
    @property
    def starting_xi(self) -> List[MyTeamPlayer]:
        """Get starting 11."""
        return [p for p in self.picks if p.position <= 11]
    
    @property
    def bench(self) -> List[MyTeamPlayer]:
        """Get bench players."""
        return [p for p in self.picks if p.position > 11]
    
    @property
    def captain_id(self) -> Optional[int]:
        """Get captain's player ID."""
        for p in self.picks:
            if p.is_captain:
                return p.element
        return None


class Transfer(BaseModel):
    """Transfer model."""
    element_in: int  # Player ID to bring in
    element_out: int  # Player ID to transfer out
    purchase_price: int
    selling_price: int


class TransferPayload(BaseModel):
    """Payload for making transfers."""
    chips: Optional[str] = None  # wildcard, freehit, etc.
    entry: int  # Team ID
    event: int  # Gameweek
    transfers: List[Transfer]


class BootstrapData(BaseModel):
    """Bootstrap static data from FPL API."""
    players: List[Player] = Field(alias="elements")
    teams: List[Team]
    events: List[GameWeek]
    element_types: List[Dict[str, Any]]
    
    class Config:
        populate_by_name = True


