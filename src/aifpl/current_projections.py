from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.artifacts import complete_artifact_paths, jsonl_bytes, verify_artifact, write_immutable, write_manifest
from aifpl.ownership import apply_effective_ownership


PROJECTION_METHOD = "fpl_source_baseline_v2.shrunk_early_season"
POSITION_PRIORS = {"GK": 3.5, "DEF": 4.0, "MID": 4.5, "FWD": 4.5}


@dataclass(frozen=True)
class CurrentPlayerProjection:
    player_id: int
    player_name: str
    position: str
    club: str
    cost: int
    projected_points: float
    availability_multiplier: float
    methodology: str = PROJECTION_METHOD
    selected_by_percent: float = 0.0
    expected_minutes: float | None = None
    appearance_probability: float | None = None
    start_probability: float | None = None
    conditional_minutes: float | None = None
    effective_ownership_pct: float | None = None
    current_evidence_weight: float | None = None
    prior_points: float | None = None
    expected_captaincy: float | None = None
    template_score: float | None = None
    template_status: str | None = None


@dataclass(frozen=True)
class ProjectionCatalog:
    source_catalog: str
    players: int
    methodology: str
    output_path: str
    created_at: datetime
    manifest_path: str | None = None


def fpl_source_baseline(
    player: CurrentPlayer,
    apply_next_round_availability: bool = True,
    gameweeks_elapsed: int | None = None,
) -> CurrentPlayerProjection:
    """Build a conservative FPL baseline with a small-sample role prior.

    ``gameweeks_elapsed=None`` retains the transparent direct-source behavior
    for callers that do not have a season clock. Production catalog builders
    always pass the elapsed gameweeks so one early result cannot become the
    player's full prior.
    """
    availability = _availability_multiplier(player, apply_next_round_availability)
    base = (0.7 * player.points_per_game + 0.3 * player.form) if player.form > 0 else player.points_per_game
    prior = POSITION_PRIORS[player.position]
    evidence_weight = _current_evidence_weight(gameweeks_elapsed)
    baseline = prior * (1 - evidence_weight) + base * evidence_weight
    return CurrentPlayerProjection(
        player_id=player.id,
        player_name=player.name,
        position=player.position,
        club=player.club,
        cost=player.cost,
        projected_points=round(baseline * availability, 4),
        availability_multiplier=availability,
        selected_by_percent=player.selected_by_percent,
        current_evidence_weight=evidence_weight if gameweeks_elapsed is not None else None,
        prior_points=prior if gameweeks_elapsed is not None else None,
    )


def _current_evidence_weight(gameweeks_elapsed: int | None) -> float:
    if gameweeks_elapsed is None:
        return 1.0
    if gameweeks_elapsed < 1:
        raise ValueError("gameweeks_elapsed must be at least 1")
    # Release current-season PPG/form gradually through the first eight GWs;
    # it never contributes more than 15% to this early-season source signal.
    return round(min(0.15, 0.10 + 0.01 * (gameweeks_elapsed - 1)), 4)


def _availability_multiplier(player: CurrentPlayer, apply_next_round_availability: bool) -> float:
    """Official FPL availability is authoritative for injuries and doubts."""
    if apply_next_round_availability:
        if player.chance_of_playing_next_round is not None:
            return player.chance_of_playing_next_round / 100
        if player.status in ("i", "u"):
            return 0.0
        if player.status == "d":
            return 0.5
    return 1.0


class CurrentProjectionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build_latest(self) -> ProjectionCatalog:
        return self.build(CurrentPlayerCatalogStore(self.root).latest_path())

    def build(self, source_path: Path) -> ProjectionCatalog:
        players = CurrentPlayerCatalogStore(self.root).load(source_path)
        gameweeks_elapsed = _elapsed_gameweeks(self.root, source_path)
        projections = apply_effective_ownership([
            fpl_source_baseline(player, gameweeks_elapsed=gameweeks_elapsed)
            for player in players
        ])
        created_at = datetime.now(timezone.utc)
        output_path = self._output_path(source_path.stem, created_at.strftime("%Y%m%dT%H%M%S%fZ"))
        write_immutable(output_path, jsonl_bytes(projections))
        manifest_path = write_manifest(
            self.root, output_path, artifact_type="current_projections", created_at=created_at.isoformat(),
            record_count=len(projections), sources={"player_catalog": source_path}, methodology=PROJECTION_METHOD,
            parameters={"gameweeks_elapsed": gameweeks_elapsed},
        )
        return ProjectionCatalog(str(source_path), len(projections), PROJECTION_METHOD, str(output_path), created_at, str(manifest_path))

    def latest(self) -> list[CurrentPlayerProjection]:
        files = complete_artifact_paths(sorted(self._catalog_dir().glob("*.jsonl"))) if self._catalog_dir().exists() else []
        if not files:
            raise FileNotFoundError("No current projections exist; run build-current-projections first")
        verify_artifact(self.root, files[-1])
        return [CurrentPlayerProjection(**json.loads(line)) for line in files[-1].read_text(encoding="utf-8").splitlines()]

    def _latest_catalog_path(self) -> Path:
        directory = self.root / "normalized" / "current" / "players"
        files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No normalized current-player catalog exists; run normalize-current-players first")
        return files[-1]

    def _catalog_dir(self) -> Path:
        return self.root / "normalized" / "current" / "projections"

    def _output_path(self, source_id: str, run_id: str) -> Path:
        return self._catalog_dir() / f"{source_id}.{run_id}.{PROJECTION_METHOD}.jsonl"


def _elapsed_gameweeks(root: Path, source_path: Path) -> int:
    snapshot_path = root / "raw" / "fpl" / "bootstrap" / f"{source_path.stem}.json"
    if snapshot_path.exists():
        try:
            document = json.loads(snapshot_path.read_text(encoding="utf-8"))
            payload = document.get("payload", document)
            events = payload.get("events", []) if isinstance(payload, dict) else []
            completed = sum(
                1 for event in events
                if isinstance(event, dict) and event.get("finished") is True
            )
            if completed:
                return completed
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    return 1
