from __future__ import annotations

from dataclasses import replace
from math import isfinite

from pydantic import BaseModel, Field

MIN_PRIOR_ADJUSTMENT = -2.0
MAX_PRIOR_ADJUSTMENT = 2.0


class PlayerPrior(BaseModel):
    player_id: int
    player_name: str
    adjustment: float = Field(ge=MIN_PRIOR_ADJUSTMENT, le=MAX_PRIOR_ADJUSTMENT)
    reason: str = Field(min_length=10)


def validate_prior_adjustment(value: float) -> float:
    value = float(value)
    if not isfinite(value) or not MIN_PRIOR_ADJUSTMENT <= value <= MAX_PRIOR_ADJUSTMENT:
        raise ValueError(
            f"adjustment must be a finite number within {MIN_PRIOR_ADJUSTMENT}..{MAX_PRIOR_ADJUSTMENT}"
        )
    return round(value, 4)


def apply_priors(rows: list[object], priors: list[PlayerPrior]) -> list[object]:
    """Return projection rows with bounded per-player additive adjustments applied.

    Rows are frozen dataclasses; adjusted rows are copies so the stored
    projection catalog is never mutated (auditability).
    """
    if not priors:
        return rows
    by_id = {prior.player_id: prior for prior in priors}
    adjusted: list[object] = []
    for row in rows:
        prior = by_id.get(getattr(row, "player_id"))
        if prior is None:
            adjusted.append(row)
            continue
        adjusted.append(replace(row, projected_points=round(row.projected_points + prior.adjustment, 4)))
    return adjusted
