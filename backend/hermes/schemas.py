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


class ChipAdvice(BaseModel):
    wildcard_now: bool = False
    free_hit_now: bool = False
    bench_boost_now: bool = False
    target_gameweeks: dict = Field(default_factory=dict)  # chip -> gw
    reason: str = ""


class TransferAdvice(BaseModel):
    out_id: int
    in_id: int
    urgency: Literal["this_week", "soon", "watch"] = "soon"
    reason: str = ""


class HermesAdjustments(BaseModel):
    """The structured output Hermes (the LLM) must produce."""
    adjustments: List[PlayerAdjustment] = Field(default_factory=list)
    captain_ranking: List[int] = Field(default_factory=list)
    triple_captain: Optional[TCAdvice] = None
    chip_advice: ChipAdvice = Field(default_factory=ChipAdvice)
    differentials: List[int] = Field(default_factory=list)
    transfer_priorities: List[TransferAdvice] = Field(default_factory=list)
    narrative: str = ""
    confidence: Literal["low", "medium", "high"] = "medium"
