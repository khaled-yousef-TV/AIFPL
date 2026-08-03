"""
Hermes output schemas.

HermesAdjustments is what the LLM must return (validated, repaired once
on failure). The bounded multiplier + ID-validation rules are the safety
contract: the LLM influences the optimizers, it never overrides them.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

try:
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing_extensions import Literal


MULTIPLIER_MIN = 0.5
MULTIPLIER_MAX = 1.5


class PlayerAdjustment(BaseModel):
    player_id: int
    multiplier: float = Field(default=1.0, ge=MULTIPLIER_MIN, le=MULTIPLIER_MAX)
    action: Literal["boost", "fade", "exclude", "lock"] = "boost"
    reason: str = ""


class TCAdvice(BaseModel):
    play_now: bool = False
    player_id: Optional[int] = None
    target_gameweek: Optional[int] = None
    reason: str = ""


class ChipProjection(BaseModel):
    """Per-chip projection conditioned on the manager's actual squad."""
    gameweek: Optional[int] = None
    confidence: Literal["low", "medium", "high"] = "low"
    reason: str = ""
    # True when the date only holds if the recommended transfers happen first
    requires_transfers: bool = False


class ChipAdvice(BaseModel):
    wildcard_now: bool = False
    free_hit_now: bool = False
    bench_boost_now: bool = False
    target_gameweeks: dict = Field(default_factory=dict)  # chip -> gw (legacy, kept for back-compat)
    # Per-chip projection: only populated for squad-aware runs. Keys are
    # "wildcard" / "free_hit" / "bench_boost" / "triple_captain".
    projection: dict = Field(default_factory=dict)
    reason: str = ""


class TransferAdvice(BaseModel):
    out_id: int
    in_id: int
    urgency: Literal["this_week", "soon", "watch"] = "soon"
    reason: str = ""


class TransferPlan(BaseModel):
    """
    Explicit verdict on whether to transfer this week or hold the free
    transfer. An empty transfer list rendered as "nothing" reads as a broken
    run — this makes "hold" a first-class recommendation with a reason.
    """
    recommendation: Literal["transfer", "hold"] = "hold"
    reason: str = ""
    # Net points the moves are expected to gain (after any -4 hit)
    expected_gain: Optional[float] = None
    hit_cost: int = 0


class HermesAdjustments(BaseModel):
    """The structured output Hermes (the LLM) must produce."""
    adjustments: List[PlayerAdjustment] = Field(default_factory=list)
    captain_ranking: List[int] = Field(default_factory=list)
    triple_captain: Optional[TCAdvice] = None
    chip_advice: ChipAdvice = Field(default_factory=ChipAdvice)
    differentials: List[int] = Field(default_factory=list)
    transfer_priorities: List[TransferAdvice] = Field(default_factory=list)
    # Explicit transfer-or-hold verdict; auto-forced to "hold" by validation
    # when transfer_priorities is empty, so a lazy run can't produce a
    # contradiction (verdict "transfer" with no moves listed).
    transfer_plan: TransferPlan = Field(default_factory=TransferPlan)
    narrative: str = ""
    confidence: Literal["low", "medium", "high"] = "medium"
