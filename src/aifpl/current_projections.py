from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.artifacts import complete_artifact_paths, jsonl_bytes, verify_artifact, write_immutable, write_manifest


PROJECTION_METHOD = "fpl_source_baseline_v1"


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


@dataclass(frozen=True)
class ProjectionCatalog:
    source_catalog: str
    players: int
    methodology: str
    output_path: str
    created_at: datetime
    manifest_path: str | None = None


def fpl_source_baseline(player: CurrentPlayer, apply_next_round_availability: bool = True) -> CurrentPlayerProjection:
    """A transparent temporary baseline using only fields supplied by FPL."""
    availability = (
        (player.chance_of_playing_next_round / 100)
        if apply_next_round_availability and player.chance_of_playing_next_round is not None else 1.0
    )
    base = (0.7 * player.points_per_game + 0.3 * player.form) if player.form > 0 else player.points_per_game
    return CurrentPlayerProjection(
        player_id=player.id,
        player_name=player.name,
        position=player.position,
        club=player.club,
        cost=player.cost,
        projected_points=round(base * availability, 4),
        availability_multiplier=availability,
        selected_by_percent=player.selected_by_percent,
    )


class CurrentProjectionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build_latest(self) -> ProjectionCatalog:
        return self.build(CurrentPlayerCatalogStore(self.root).latest_path())

    def build(self, source_path: Path) -> ProjectionCatalog:
        players = CurrentPlayerCatalogStore(self.root).load(source_path)
        projections = [fpl_source_baseline(player) for player in players]
        created_at = datetime.now(timezone.utc)
        output_path = self._output_path(source_path.stem, created_at.strftime("%Y%m%dT%H%M%S%fZ"))
        write_immutable(output_path, jsonl_bytes(projections))
        manifest_path = write_manifest(
            self.root, output_path, artifact_type="current_projections", created_at=created_at.isoformat(),
            record_count=len(projections), sources={"player_catalog": source_path}, methodology=PROJECTION_METHOD,
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
