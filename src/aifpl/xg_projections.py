from __future__ import annotations

import json
import math
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
    appearance_probability: float | None = None
    start_probability: float | None = None
    conditional_minutes: float | None = None
    availability_multiplier: float | None = None
    selected_by_percent: float = 0.0
    effective_ownership_pct: float | None = None


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
    appearance_probability_override: float | None = None,
    conditional_minutes_override: float | None = None,
) -> XgXaProjection:
    if gameweeks_elapsed is not None and gameweeks_elapsed < 1:
        raise ValueError("gameweeks_elapsed must be at least 1")
    if minutes_multiplier_override is not None and not 0 < minutes_multiplier_override <= 1.5:
        raise ValueError("minutes_multiplier_override must be within 0..1.5")
    _validate_probability(start_probability_override, "start_probability_override")
    _validate_probability(appearance_probability_override, "appearance_probability_override")
    if conditional_minutes_override is not None and not math.isfinite(conditional_minutes_override):
        raise ValueError("conditional_minutes_override must be finite")
    if conditional_minutes_override is not None and not 0 <= conditional_minutes_override <= 90:
        raise ValueError("conditional_minutes_override must be within 0..90")
    availability = (
        (player.chance_of_playing_next_round / 100)
        if apply_next_round_availability and player.chance_of_playing_next_round is not None
        else (0.0 if apply_next_round_availability and player.status in ("i", "u")
              else (0.5 if apply_next_round_availability and player.status == "d" else 1.0))
    )
    appearance_probability, start_probability, conditional_minutes = _participation_components(
        player,
        gameweeks_elapsed,
        transfer_profile,
        start_probability_override,
        appearance_probability_override,
        conditional_minutes_override,
    )
    # Keep selection risk, match availability, and minutes conditional on an
    # appearance as separate factors.  In particular, a substitute-heavy
    # player must not be penalized once for appearing and again for not starting.
    expected_minutes = conditional_minutes * appearance_probability * availability
    if transfer_profile is not None:
        expected_minutes *= transfer_profile.minutes_multiplier
    if minutes_multiplier_override is not None:
        expected_minutes *= minutes_multiplier_override
    expected_minutes = min(90.0, max(0.0, expected_minutes))
    if player.minutes > 0:
        xg_per_90 = _rate_with_prior(player.expected_goals / player.minutes * 90, transfer_profile, "prior_goals_per_90", player.minutes)
        xa_per_90 = _rate_with_prior(player.expected_assists / player.minutes * 90, transfer_profile, "prior_assists_per_90", player.minutes)
        xgi_per_90 = xg_per_90 + xa_per_90 if transfer_profile is not None and transfer_profile.has_prior_stats else player.expected_goal_involvements / player.minutes * 90
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
    # FPL PPG is an average for games in which the player appears.  Scale it by
    # the appearance probability once, rather than also scaling it by the
    # conditional minutes used by the xG component.
    source_participation = appearance_probability * availability
    if transfer_profile is not None:
        source_participation *= transfer_profile.minutes_multiplier
    if minutes_multiplier_override is not None:
        source_participation *= minutes_multiplier_override
    source_baseline = fpl_source_baseline(
        player,
        apply_next_round_availability=False,
        gameweeks_elapsed=gameweeks_elapsed,
    ).projected_points * source_participation
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
        appearance_probability=round(appearance_probability, 4),
        start_probability=round(start_probability, 4),
        conditional_minutes=round(conditional_minutes, 4),
        availability_multiplier=round(availability, 4),
        selected_by_percent=player.selected_by_percent,
    )


def _participation_components(
    player: CurrentPlayer,
    gameweeks_elapsed: int | None,
    transfer_profile: TransferProfile | None,
    start_probability_override: float | None,
    appearance_probability_override: float | None,
    conditional_minutes_override: float | None,
) -> tuple[float, float, float]:
    observed_appearances = _estimated_appearances(player)
    opportunities = max(
        1,
        gameweeks_elapsed if gameweeks_elapsed is not None else 0,
        observed_appearances,
        max(0, player.starts),
    )
    raw_start = min(1.0, max(0, player.starts) / opportunities)
    start_probability = start_probability_override if start_probability_override is not None else raw_start

    if observed_appearances:
        raw_appearance = min(1.0, observed_appearances / opportunities)
        # A start is certain evidence of an appearance.  When the minute total
        # implies extra appearances, shrink that uncertain excess toward the
        # observed starting rate instead of treating every inferred appearance
        # as equally reliable.
        prior_weight = min(4.0, max(0.0, 4.0 - player.starts)) if observed_appearances > player.starts else 0.0
        prior_appearance = raw_start
        appearance_probability = (
            (raw_appearance * opportunities) + prior_appearance * prior_weight
        ) / (opportunities + prior_weight)
        conditional_minutes = min(90.0, max(0.0, player.minutes / observed_appearances))
    elif transfer_profile is not None and transfer_profile.has_prior_stats:
        # There is no current-season participation evidence, but imported prior
        # production gives a conservative opportunity prior for a new context.
        appearance_probability = 0.5
        conditional_minutes = 60.0
        start_probability = start_probability_override if start_probability_override is not None else 0.5
    else:
        appearance_probability = 0.0
        conditional_minutes = 0.0

    if appearance_probability_override is not None:
        appearance_probability = appearance_probability_override
    elif start_probability_override is not None:
        # A predicted start is also positive evidence that the player appears.
        appearance_probability = max(appearance_probability, start_probability_override)
        if conditional_minutes == 0:
            conditional_minutes = 90.0
    if conditional_minutes_override is not None:
        conditional_minutes = conditional_minutes_override
    return (
        min(1.0, max(0.0, appearance_probability)),
        min(1.0, max(0.0, start_probability)),
        min(90.0, max(0.0, conditional_minutes)),
    )


def _estimated_appearances(player: CurrentPlayer) -> int:
    if player.minutes <= 0:
        return 0
    return max(max(0, player.starts), int(math.ceil(player.minutes / 90)))


def _rate_with_prior(current: float, profile: TransferProfile | None, field: str, minutes: int) -> float:
    if profile is None or not profile.has_prior_stats:
        return current
    prior = getattr(profile, field) * NEW_CONTEXT_STAT_DECAY
    # Give prior-season rates the most influence when the current sample is
    # small, while allowing a long current-season sample to take over.
    prior_weight = min(0.5, 900 / (max(1, minutes) + 900))
    return current * (1 - prior_weight) + prior * prior_weight


def _validate_probability(value: float | None, name: str) -> None:
    if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError(f"{name} must be within 0..1")


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
