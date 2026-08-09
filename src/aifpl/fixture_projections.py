from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayer
from aifpl.current_projections import CurrentPlayerProjection, fpl_source_baseline
from aifpl.fixtures import CurrentFixture
from aifpl.artifacts import complete_artifact_paths, jsonl_bytes, verify_artifact, write_immutable, write_manifest


DIFFICULTY_MULTIPLIERS = {1: 1.15, 2: 1.075, 3: 1.0, 4: 0.925, 5: 0.85}
FIXTURE_PROJECTION_METHOD = "fpl_source_baseline_v1.fixture_difficulty_v1"


@dataclass(frozen=True)
class FixtureGameweekProjection:
    player_id: int
    player_name: str
    position: str
    club: str
    cost: int
    gameweek: int
    fixture_count: int
    average_difficulty: float | None
    projected_points: float
    methodology: str = FIXTURE_PROJECTION_METHOD


@dataclass(frozen=True)
class FixtureProjectionCatalog:
    start_gameweek: int
    end_gameweek: int
    records: int
    output_path: str
    created_at: datetime
    source_player_catalog: str | None = None
    source_fixture_catalog: str | None = None
    methodology: str = FIXTURE_PROJECTION_METHOD
    manifest_path: str | None = None


def build_fixture_gameweek_projections(
    players: list[CurrentPlayer], fixtures: list[CurrentFixture], start_gameweek: int, end_gameweek: int
) -> list[FixtureGameweekProjection]:
    if start_gameweek < 1 or end_gameweek < start_gameweek:
        raise ValueError("gameweek range is invalid")
    by_team_gameweek: dict[tuple[int, int], list[int]] = {}
    for fixture in fixtures:
        if fixture.finished or fixture.gameweek is None or not start_gameweek <= fixture.gameweek <= end_gameweek:
            continue
        by_team_gameweek.setdefault((fixture.home_team_id, fixture.gameweek), []).append(fixture.home_difficulty)
        by_team_gameweek.setdefault((fixture.away_team_id, fixture.gameweek), []).append(fixture.away_difficulty)
    projections: list[FixtureGameweekProjection] = []
    for player in players:
        for gameweek in range(start_gameweek, end_gameweek + 1):
            baseline = fpl_source_baseline(player, apply_next_round_availability=gameweek == start_gameweek)
            difficulties = by_team_gameweek.get((player.club_id, gameweek), [])
            multipliers = [DIFFICULTY_MULTIPLIERS[difficulty] for difficulty in difficulties]
            projections.append(FixtureGameweekProjection(
                player_id=player.id,
                player_name=player.name,
                position=player.position,
                club=player.club,
                cost=player.cost,
                gameweek=gameweek,
                fixture_count=len(difficulties),
                average_difficulty=round(sum(difficulties) / len(difficulties), 4) if difficulties else None,
                projected_points=round(baseline.projected_points * sum(multipliers), 4),
            ))
    return projections


class FixtureProjectionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build(
        self, start_gameweek: int, end_gameweek: int,
        player_catalog_path: Path | None = None, fixture_catalog_path: Path | None = None,
    ) -> FixtureProjectionCatalog:
        from aifpl.current import CurrentPlayerCatalogStore
        from aifpl.fixtures import CurrentFixtureCatalogStore

        player_store = CurrentPlayerCatalogStore(self.root)
        fixture_store = CurrentFixtureCatalogStore(self.root)
        player_path = player_catalog_path or player_store.latest_path()
        fixture_path = fixture_catalog_path or fixture_store.latest_path()
        players = player_store.load(player_path)
        fixtures = fixture_store.load(fixture_path)
        projections = build_fixture_gameweek_projections(players, fixtures, start_gameweek, end_gameweek)
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = self.root / "normalized" / "current" / "fixture_projections" / f"gw{start_gameweek}-{end_gameweek}.{run_id}.{FIXTURE_PROJECTION_METHOD}.jsonl"
        write_immutable(output_path, jsonl_bytes(projections))
        manifest_path = write_manifest(
            self.root, output_path, artifact_type="fixture_projections", created_at=created_at.isoformat(),
            record_count=len(projections), sources={"player_catalog": player_path, "fixture_catalog": fixture_path},
            methodology=FIXTURE_PROJECTION_METHOD,
            parameters={"start_gameweek": start_gameweek, "end_gameweek": end_gameweek, "difficulty_multipliers": DIFFICULTY_MULTIPLIERS},
        )
        return FixtureProjectionCatalog(
            start_gameweek, end_gameweek, len(projections), str(output_path), created_at,
            str(player_path), str(fixture_path), FIXTURE_PROJECTION_METHOD, str(manifest_path),
        )

    def latest_path(self) -> Path:
        directory = self.root / "normalized" / "current" / "fixture_projections"
        files = complete_artifact_paths(list(directory.glob("*.jsonl"))) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No fixture projections exist; run build-fixture-projections first")
        timestamped = [path for path in files if len(path.name.split(".")) >= 4]
        return max(timestamped or files, key=lambda path: path.name.split(".")[1] if len(path.name.split(".")) >= 4 else path.name)

    def latest(self, catalog_id: str | None = None) -> list[FixtureGameweekProjection]:
        path = self._catalog_path(catalog_id) if catalog_id else self.latest_path()
        verify_artifact(self.root, path, require_manifest=catalog_id is not None)
        return [FixtureGameweekProjection(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]

    def _catalog_path(self, catalog_id: str) -> Path:
        if Path(catalog_id).name != catalog_id or not catalog_id.endswith(".jsonl"):
            raise ValueError("catalog_id must be a projection JSONL filename")
        path = self.root / "normalized" / "current" / "fixture_projections" / catalog_id
        if not path.exists():
            raise FileNotFoundError(f"Fixture projection catalog does not exist: {catalog_id}")
        return path
