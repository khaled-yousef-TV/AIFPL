from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.current_projections import fpl_source_baseline
from aifpl.transfer_awareness import NEW_CONTEXT_STAT_DECAY, TransferProfile, TransferAwarenessStore
from aifpl.artifacts import complete_artifact_paths, jsonl_bytes, verify_artifact, write_immutable, write_manifest


XG_XA_PROJECTION_METHOD = "fpl_xg_xa_blend_v2"
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
    manifest_path: str | None = None


def xg_xa_blend(
    player: CurrentPlayer, gameweeks_elapsed: int | None = None,
    apply_next_round_availability: bool = True,
    start_probability_override: float | None = None,
    transfer_profile: TransferProfile | None = None,
    minutes_multiplier_override: float | None = None,
) -> XgXaProjection:
    if gameweeks_elapsed is not None and gameweeks_elapsed < 1:
        raise ValueError("gameweeks_elapsed must be at least 1")
    if minutes_multiplier_override is not None and not 0 < minutes_multiplier_override <= 1.5:
        raise ValueError("minutes_multiplier_override must be within 0..1.5")
    availability = (
        (player.chance_of_playing_next_round / 100)
        if apply_next_round_availability and player.chance_of_playing_next_round is not None else 1.0
    )
    expected_minutes = min(90.0, (player.minutes / player.starts) if player.starts else 0.0)
    # Starts measure availability only relative to opportunities already elapsed.
    opportunities = gameweeks_elapsed if gameweeks_elapsed is not None else max(1, player.starts)
    start_probability = (
        start_probability_override if start_probability_override is not None
        else min(1.0, player.starts / opportunities)
    )
    if not 0 <= start_probability <= 1:
        raise ValueError("start_probability_override must be within 0..1")
    expected_minutes *= start_probability * availability
    if transfer_profile is not None:
        expected_minutes *= transfer_profile.minutes_multiplier
    if minutes_multiplier_override is not None:
        expected_minutes *= minutes_multiplier_override
    if player.minutes > 0:
        xg_per_90 = player.expected_goals / player.minutes * 90
        xa_per_90 = player.expected_assists / player.minutes * 90
        xgi_per_90 = player.expected_goal_involvements / player.minutes * 90
        xgc_per_90 = player.expected_goals_conceded / player.minutes * 90
    elif transfer_profile is not None and transfer_profile.has_prior_stats:
        xg_per_90 = transfer_profile.prior_goals_per_90 * NEW_CONTEXT_STAT_DECAY
        xa_per_90 = transfer_profile.prior_assists_per_90 * NEW_CONTEXT_STAT_DECAY
        xgi_per_90 = xg_per_90 + xa_per_90
        xgc_per_90 = 0.0
    else:
        xg_per_90 = xa_per_90 = xgi_per_90 = xgc_per_90 = 0.0
    minute_fraction = expected_minutes / 90
    attacking_points = min(2.0, expected_minutes / 45) + (
        xg_per_90 * minute_fraction * GOAL_POINTS[player.position]
    ) + (xa_per_90 * minute_fraction * 3)
    # Source points-per-game is conditional on appearing, so scale it by expected participation too.
    source_baseline = fpl_source_baseline(player, apply_next_round_availability=False).projected_points * minute_fraction
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


def elapsed_gameweeks(root: Path, player_catalog_path: Path, players: list[CurrentPlayer]) -> int:
    snapshot_path = root / "raw" / "fpl" / "bootstrap" / f"{player_catalog_path.stem}.json"
    if snapshot_path.exists():
        events = json.loads(snapshot_path.read_text(encoding="utf-8")).get("payload", {}).get("events", [])
        completed = sum(1 for event in events if isinstance(event, dict) and event.get("finished") is True)
        if completed:
            return completed
    return max(1, max((player.starts for player in players), default=0))


class XgXaProjectionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build_latest(self) -> XgXaProjectionCatalog:
        return self.build(CurrentPlayerCatalogStore(self.root).latest_path())

    def build(self, source_path: Path) -> XgXaProjectionCatalog:
        players = CurrentPlayerCatalogStore(self.root).load(source_path)
        gameweeks_elapsed = elapsed_gameweeks(self.root, source_path, players)
        profiles = TransferAwarenessStore(self.root).latest(players)
        projections = [xg_xa_blend(player, gameweeks_elapsed, transfer_profile=profiles.get(player.id)) for player in players]
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = self.root / "normalized" / "current" / "xg_xa_projections" / f"{source_path.stem}.{run_id}.{XG_XA_PROJECTION_METHOD}.jsonl"
        write_immutable(output_path, jsonl_bytes(projections))
        manifest_path = write_manifest(
            self.root, output_path, artifact_type="xg_xa_projections", created_at=created_at.isoformat(),
            record_count=len(projections), sources={"player_catalog": source_path}, methodology=XG_XA_PROJECTION_METHOD,
            parameters={"gameweeks_elapsed": gameweeks_elapsed},
        )
        return XgXaProjectionCatalog(str(source_path), len(projections), str(output_path), created_at, XG_XA_PROJECTION_METHOD, str(manifest_path))

    def latest(self) -> list[XgXaProjection]:
        directory = self.root / "normalized" / "current" / "xg_xa_projections"
        files = complete_artifact_paths(sorted(directory.glob("*.jsonl"))) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No xG/xA projections exist; run build-xg-xa-projections first")
        verify_artifact(self.root, files[-1])
        return [XgXaProjection(**json.loads(line)) for line in files[-1].read_text(encoding="utf-8").splitlines()]

    def _latest_catalog_path(self) -> Path:
        directory = self.root / "normalized" / "current" / "players"
        files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No normalized current-player catalog exists; run normalize-current-players first")
        return files[-1]
