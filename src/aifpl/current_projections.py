from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore


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


@dataclass(frozen=True)
class ProjectionCatalog:
    source_catalog: str
    players: int
    methodology: str
    output_path: str
    created_at: datetime


def fpl_source_baseline(player: CurrentPlayer) -> CurrentPlayerProjection:
    """A transparent temporary baseline using only fields supplied by FPL."""
    availability = (player.chance_of_playing_next_round / 100) if player.chance_of_playing_next_round is not None else 1.0
    base = (0.7 * player.points_per_game + 0.3 * player.form) if player.form > 0 else player.points_per_game
    return CurrentPlayerProjection(
        player_id=player.id,
        player_name=player.name,
        position=player.position,
        club=player.club,
        cost=player.cost,
        projected_points=round(base * availability, 4),
        availability_multiplier=availability,
    )


class CurrentProjectionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build_latest(self) -> ProjectionCatalog:
        source_path = self._latest_catalog_path()
        players = CurrentPlayerCatalogStore(self.root).latest_players()
        projections = [fpl_source_baseline(player) for player in players]
        output_path = self._output_path(source_path.stem)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in projections), encoding="utf-8")
        created_at = datetime.now(timezone.utc)
        return ProjectionCatalog(str(source_path), len(projections), PROJECTION_METHOD, str(output_path), created_at)

    def latest(self) -> list[CurrentPlayerProjection]:
        files = sorted(self._catalog_dir().glob("*.jsonl")) if self._catalog_dir().exists() else []
        if not files:
            raise FileNotFoundError("No current projections exist; run build-current-projections first")
        return [CurrentPlayerProjection(**json.loads(line)) for line in files[-1].read_text(encoding="utf-8").splitlines()]

    def _latest_catalog_path(self) -> Path:
        directory = self.root / "normalized" / "current" / "players"
        files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No normalized current-player catalog exists; run normalize-current-players first")
        return files[-1]

    def _catalog_dir(self) -> Path:
        return self.root / "normalized" / "current" / "projections"

    def _output_path(self, source_id: str) -> Path:
        return self._catalog_dir() / f"{source_id}.{PROJECTION_METHOD}.jsonl"
