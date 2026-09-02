from __future__ import annotations

import json
import math
import os
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, Mapping, TypeVar


OwnershipRow = TypeVar("OwnershipRow")


def configured_effective_ownership() -> dict[int, float]:
    """Load optional externally-derived EO without treating public ownership as EO."""
    configured = os.environ.get("AIFPL_EFFECTIVE_OWNERSHIP_FILE")
    if not configured:
        return {}
    path = Path(configured).expanduser()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"Cannot read effective ownership file: {path}") from exc
    values = document.get("players", document) if isinstance(document, dict) else None
    if not isinstance(values, Mapping):
        raise ValueError("Effective ownership file must be an object or contain a players object")
    return _validate_ownership(values)


def apply_effective_ownership(rows: list[OwnershipRow], ownership: dict[int, float] | None = None) -> list[OwnershipRow]:
    values = configured_effective_ownership() if ownership is None else _validate_ownership(ownership)
    return [
        _with_effective_ownership(row, values[_player_id(row)])
        if _player_id(row) in values else row
        for row in rows
    ]


def _validate_ownership(values: Mapping[Any, Any]) -> dict[int, float]:
    ownership: dict[int, float] = {}
    for player_id, value in values.items():
        if isinstance(value, Mapping):
            value = value.get("effective_ownership_pct", value.get("effective_ownership"))
        try:
            identifier, percentage = int(player_id), float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Effective ownership entries must map player IDs to numbers") from exc
        if identifier <= 0 or not math.isfinite(percentage) or not 0 <= percentage <= 300:
            raise ValueError("Effective ownership must be a finite percentage within 0..300")
        ownership[identifier] = percentage
    return ownership


def _player_id(row: object) -> int:
    try:
        value = row.get("player_id") if isinstance(row, Mapping) else getattr(row, "player_id")
        return int(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise TypeError("Ownership rows must expose a player_id") from exc


def _with_effective_ownership(row: OwnershipRow, value: float) -> OwnershipRow:
    if isinstance(row, Mapping):
        enriched = dict(row)
        enriched["effective_ownership_pct"] = value
        return enriched  # type: ignore[return-value]
    if hasattr(row, "model_copy"):
        return row.model_copy(update={"effective_ownership_pct": value})
    try:
        names = {field.name for field in fields(row)}
    except TypeError as exc:
        raise TypeError("Ownership rows must support dataclass replacement or model_copy") from exc
    if "effective_ownership_pct" not in names:
        raise TypeError("Ownership rows must define optional effective_ownership_pct")
    return replace(row, effective_ownership_pct=value)
