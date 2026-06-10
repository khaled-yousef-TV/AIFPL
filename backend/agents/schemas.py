"""
Signal schemas for the Hermes agent layer.

Every agent returns an AgentReport envelope whose `payload` is the
model_dump() of one of the typed payload models below.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

try:
    from typing import Literal
except ImportError:  # pragma: no cover - py<3.8 fallback, not expected
    from typing_extensions import Literal


# ==================== Envelope ====================

class AgentReport(BaseModel):
    """Common envelope returned by every agent."""
    agent: str
    version: str = "1"
    gameweek: int
    generated_at: datetime
    status: Literal["ok", "degraded", "error"] = "ok"
    elapsed_ms: int = 0
    summary: str = ""  # <= ~400 chars; the digest Hermes always sees
    payload: Dict[str, Any] = Field(default_factory=dict)


# ==================== Data agent ====================

class PlayerSnapshot(BaseModel):
    id: int
    name: str
    team: str
    team_id: int
    position: str
    position_id: int
    price: float
    form: float
    predicted_points: float
    points_per_game: float = 0.0
    total_points: int = 0
    ownership: float
    xGI: float = 0.0
    xGC: float = 0.0
    opponent: str = "???"
    is_home: bool = False
    fixture_difficulty: int = 3
    status: str = "a"
    in_user_team: bool = False
    # Cold-start: last season's baseline (None outside early-season blending)
    prior_ppg: Optional[float] = None
    prior_total_points: Optional[int] = None


class DataSignals(BaseModel):
    gameweek_deadline: Optional[datetime] = None
    season_phase: str = "mid"  # preseason/early/mid/run_in/off_season
    prior_season_available: bool = False
    players: List[PlayerSnapshot] = Field(default_factory=list)


# ==================== Betting agent ====================

class FixtureOdds(BaseModel):
    home_team: str
    away_team: str
    home_win_prob: Optional[float] = None
    away_win_prob: Optional[float] = None
    btts_prob: Optional[float] = None
    home_clean_sheet_prob: Optional[float] = None
    away_clean_sheet_prob: Optional[float] = None


class PlayerScorerOdds(BaseModel):
    id: int
    name: str
    team: str
    anytime_scorer_prob: float


class MarketEdge(BaseModel):
    """Where the betting market disagrees materially with the model."""
    id: int
    name: str
    team: str
    direction: Literal["market_higher", "market_lower"]
    note: str


class BettingSignals(BaseModel):
    enabled: bool = False
    fixtures: List[FixtureOdds] = Field(default_factory=list)
    scorer_odds: List[PlayerScorerOdds] = Field(default_factory=list)
    edges: List[MarketEdge] = Field(default_factory=list)


# ==================== Mechanics agent ====================

class GameweekFixtureLoad(BaseModel):
    gameweek: int
    double_teams: List[str] = Field(default_factory=list)  # 2+ fixtures
    blank_teams: List[str] = Field(default_factory=list)   # 0 fixtures


class PriceChangeCandidate(BaseModel):
    id: int
    name: str
    team: str
    price: float
    transfer_balance: int  # transfers_in_event - transfers_out_event
    direction: Literal["rise", "fall"]


class SquadRules(BaseModel):
    squad_size: int = 15
    starting_xi: int = 11
    budget: float = 100.0
    max_per_team: int = 3
    positions: Dict[str, int] = Field(
        default_factory=lambda: {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
    )
    chips: List[str] = Field(
        default_factory=lambda: [
            "wildcard", "free_hit", "bench_boost", "triple_captain"
        ]
    )


class MechanicsSignals(BaseModel):
    current_gameweek: int
    next_gameweek: int
    season_phase: Literal["preseason", "early", "mid", "run_in", "off_season"] = "mid"
    finished_gameweeks: int = 0
    next_deadline: Optional[datetime] = None
    hours_to_deadline: Optional[float] = None
    fixture_load: List[GameweekFixtureLoad] = Field(default_factory=list)
    # Season planning: avg FDR per team over the next 6 GWs (lower = easier run)
    team_next6_fdr: Dict[str, float] = Field(default_factory=dict)
    price_rise_candidates: List[PriceChangeCandidate] = Field(default_factory=list)
    price_fall_candidates: List[PriceChangeCandidate] = Field(default_factory=list)
    squad_rules: SquadRules = Field(default_factory=SquadRules)
    chip_guidance: List[str] = Field(default_factory=list)


# ==================== Availability agent ====================

class AvailabilityFlag(BaseModel):
    id: int
    name: str
    team: str
    status: str
    chance_of_playing: Optional[int] = None
    news: str = ""
    rotation_risk: str = "none"     # none/low/medium/high
    rotation_reason: str = ""
    flag_reason: str = ""           # why this player was flagged


class AvailabilitySignals(BaseModel):
    flagged: List[AvailabilityFlag] = Field(default_factory=list)
    high_rotation_risk_teams: List[str] = Field(default_factory=list)


# ==================== Form agent ====================

class FormEntry(BaseModel):
    id: int
    name: str
    team: str
    position: str
    form: float
    points_per_game: float
    delta: float  # form - ppg (positive = hot, negative = cold)


class TeamTrendEntry(BaseModel):
    team: str
    strength: int
    season_ppm: float
    recent_ppm: float
    momentum: float
    reversal_score: float


class FormSignals(BaseModel):
    hot_players: List[FormEntry] = Field(default_factory=list)
    cold_players: List[FormEntry] = Field(default_factory=list)
    team_trends: List[TeamTrendEntry] = Field(default_factory=list)


# ==================== Variability agent ====================

class VariabilityEntry(BaseModel):
    id: int
    name: str
    team: str
    position: str
    source: Literal["current", "prior"] = "current"  # prior = last-season archive (cold start)
    n_gws: int
    mean_pts: float
    stddev: float
    cv: float                # coefficient of variation (stddev/mean)
    ceiling_p90: float
    floor_p10: float
    haul_rate: float         # share of GWs with >= 10 pts
    blank_rate: float        # share of GWs with <= 2 pts
    form_recent: float = 0.0  # mean of last few appearances
    consistency_score: float  # 0..1, higher = more consistent


class VariabilitySignals(BaseModel):
    pool_size: int = 0
    covered: int = 0
    players: List[VariabilityEntry] = Field(default_factory=list)
    captaincy_candidates: List[int] = Field(default_factory=list)   # high ceiling
    core_candidates: List[int] = Field(default_factory=list)        # high consistency


# ==================== News agent (Phase 3) ====================

class NewsItem(BaseModel):
    player_id: Optional[int] = None
    team: Optional[str] = None
    headline: str
    summary: str = ""
    sentiment: float = 0.0  # -1..1
    impact: Literal["out", "doubt", "boost", "neutral", "incentive"] = "neutral"
    incentive_type: Optional[
        Literal["record_chase", "golden_boot", "milestone",
                "contract", "call_up", "revenge", "other"]
    ] = None
    behavioral_implication: Optional[str] = None
    source_url: Optional[str] = None


class NewsSignals(BaseModel):
    search_used: bool = False
    items: List[NewsItem] = Field(default_factory=list)
