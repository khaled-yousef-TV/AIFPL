from __future__ import annotations

import math
from collections import defaultdict
from enum import Enum
from pathlib import Path

from aifpl.current_projections import CurrentPlayerProjection, CurrentProjectionStore
from aifpl.fixture_projections import FixtureProjectionStore
from aifpl.odds_projections import OddsProjectionStore
from aifpl.xg_projections import XgXaProjectionStore


class ProjectionSource(str, Enum):
    CURRENT = "current"
    XG_XA = "xg-xa"
    FIXTURE = "fixture"
    ODDS = "odds"


def load_projection_candidates(
    root: Path, source: ProjectionSource, catalog_id: str | None = None,
) -> list[CurrentPlayerProjection]:
    if catalog_id is not None and source not in (ProjectionSource.FIXTURE, ProjectionSource.ODDS):
        raise ValueError("catalog_id is supported only for fixture and odds projection sources")
    if source == ProjectionSource.CURRENT:
        return CurrentProjectionStore(root).latest()
    if source == ProjectionSource.XG_XA:
        rows = XgXaProjectionStore(root).latest()
    elif source == ProjectionSource.FIXTURE:
        return _aggregate(FixtureProjectionStore(root).latest(catalog_id))
    else:
        return _aggregate(OddsProjectionStore(root).latest(catalog_id))
    return [
        CurrentPlayerProjection(
            player_id=row.player_id, player_name=row.player_name, position=row.position,
            club=row.club, cost=row.cost, projected_points=row.projected_points,
            availability_multiplier=1.0, methodology=row.methodology,
            selected_by_percent=getattr(row, "selected_by_percent", 0.0),
        )
        for row in rows
    ]


def _aggregate(rows: list[object]) -> list[CurrentPlayerProjection]:
    grouped: dict[int, list[object]] = defaultdict(list)
    seen: set[tuple[int, int]] = set()
    for row in rows:
        key = (row.player_id, row.gameweek)
        if key in seen:
            raise ValueError(f"Duplicate projection for player {row.player_id}, gameweek {row.gameweek}")
        seen.add(key)
        grouped[row.player_id].append(row)
    candidates: list[CurrentPlayerProjection] = []
    expected_gameweeks = {row.gameweek for row in rows}
    for player_id, player_rows in sorted(grouped.items()):
        first = player_rows[0]
        metadata = {(row.player_name, row.position, row.club, row.cost, row.methodology, getattr(row, "selected_by_percent", 0.0)) for row in player_rows}
        if len(metadata) != 1:
            raise ValueError(f"Inconsistent projection metadata for player {player_id}")
        if {row.gameweek for row in player_rows} != expected_gameweeks:
            raise ValueError(f"Incomplete gameweek projection set for player {player_id}")
        points = sum(row.projected_points for row in player_rows)
        if not math.isfinite(points):
            raise ValueError(f"Non-finite projection for player {player_id}")
        candidates.append(CurrentPlayerProjection(
            player_id=player_id, player_name=first.player_name, position=first.position,
            club=first.club, cost=first.cost, projected_points=round(points, 4),
            availability_multiplier=1.0, methodology=first.methodology,
            selected_by_percent=getattr(first, "selected_by_percent", 0.0),
        ))
    if not candidates:
        raise ValueError("Projection catalog is empty")
    return candidates
