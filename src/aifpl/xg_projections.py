from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.current_projections import fpl_source_baseline


XG_XA_PROJECTION_METHOD = "fpl_xg_xa_blend_v1"
GOAL_POINTS = {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4}


@dataclass(frozen=True)
class XgXaProjection:
    player_id: int
    player_name: str
    position: str
    club: str
    cost: int
    expected_minutes: float
    xg_per_90: float
    xa_per_90: float
    xgi_per_90: float
    xgc_per_90: float
    attacking_points_estimate: float
    projected_points: float
    methodology: str = XG_XA_PROJECTION_METHOD


@dataclass(frozen=True)
class XgXaProjectionCatalog:
    source_catalog: str
    players: int
    output_path: str
    created_at: datetime
    methodology: str = XG_XA_PROJECTION_METHOD


def xg_xa_blend(player: CurrentPlayer, season_gameweeks: int = 38) -> XgXaProjection:
    if season_gameweeks < 1:
        raise ValueError("season_gameweeks must be at least 1")
    availability = (player.chance_of_playing_next_round / 100) if player.chance_of_playing_next_round is not None else 1.0
    expected_minutes = min(90.0, (player.minutes / player.starts) if player.starts else 0.0)
    expected_minutes *= min(1.0, player.starts / season_gameweeks) * availability
    if player.minutes > 0:
        xg_per_90 = player.expected_goals / player.minutes * 90
        xa_per_90 = player.expected_assists / player.minutes * 90
        xgi_per_90 = player.expected_goal_involvements / player.minutes * 90
        xgc_per_90 = player.expected_goals_conceded / player.minutes * 90
    else:
        xg_per_90 = xa_per_90 = xgi_per_90 = xgc_per_90 = 0.0
    minute_fraction = expected_minutes / 90
    attacking_points = min(2.0, expected_minutes / 45) + (
        xg_per_90 * minute_fraction * GOAL_POINTS[player.position]
    ) + (xa_per_90 * minute_fraction * 3)
    source_baseline = fpl_source_baseline(player).projected_points
    return XgXaProjection(
        player_id=player.id,
        player_name=player.name,
        position=player.position,
        club=player.club,
        cost=player.cost,
        expected_minutes=round(expected_minutes, 4),
        xg_per_90=round(xg_per_90, 4),
        xa_per_90=round(xa_per_90, 4),
        xgi_per_90=round(xgi_per_90, 4),
        xgc_per_90=round(xgc_per_90, 4),
        attacking_points_estimate=round(attacking_points, 4),
        projected_points=round(0.6 * source_baseline + 0.4 * attacking_points, 4),
    )


class XgXaProjectionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build_latest(self) -> XgXaProjectionCatalog:
        source_path = self._latest_catalog_path()
        projections = [xg_xa_blend(player) for player in CurrentPlayerCatalogStore(self.root).latest_players()]
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = self.root / "normalized" / "current" / "xg_xa_projections" / f"{source_path.stem}.{run_id}.{XG_XA_PROJECTION_METHOD}.jsonl"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in projections), encoding="utf-8")
        return XgXaProjectionCatalog(str(source_path), len(projections), str(output_path), created_at)

    def latest(self) -> list[XgXaProjection]:
        directory = self.root / "normalized" / "current" / "xg_xa_projections"
        files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No xG/xA projections exist; run build-xg-xa-projections first")
        return [XgXaProjection(**json.loads(line)) for line in files[-1].read_text(encoding="utf-8").splitlines()]

    def _latest_catalog_path(self) -> Path:
        directory = self.root / "normalized" / "current" / "players"
        files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No normalized current-player catalog exists; run normalize-current-players first")
        return files[-1]
