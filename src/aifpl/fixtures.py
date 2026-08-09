from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from aifpl.artifacts import complete_artifact_paths, jsonl_bytes, verify_artifact, write_immutable, write_manifest


@dataclass(frozen=True)
class CurrentFixture:
    id: int
    gameweek: int | None
    kickoff_time: str | None
    home_team_id: int
    away_team_id: int
    home_difficulty: int
    away_difficulty: int
    finished: bool


@dataclass(frozen=True)
class FixtureCatalog:
    source_snapshot: str
    fetched_at: datetime
    fixtures: int
    output_path: str
    manifest_path: str | None = None


class CurrentFixtureCatalogStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def normalize_latest(self) -> FixtureCatalog:
        return self.normalize(self._latest_raw_path())

    def normalize(self, source_path: Path) -> FixtureCatalog:
        document = json.loads(source_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(document["metadata"]["fetched_at"])
        fixtures = normalize_fixtures(document["payload"])
        output_path = self._output_path(source_path.stem)
        if output_path.exists():
            if self.load(output_path) != fixtures:
                raise ValueError(f"Existing fixture catalog disagrees with source snapshot: {output_path}")
        else:
            write_immutable(output_path, jsonl_bytes(fixtures))
        manifest_path = output_path.with_suffix(".manifest.json")
        if not manifest_path.exists():
            manifest_path = write_manifest(
                self.root, output_path, artifact_type="current_fixtures", created_at=fetched_at.isoformat(),
                record_count=len(fixtures), sources={"fixture_snapshot": source_path},
            )
        return FixtureCatalog(str(source_path), fetched_at, len(fixtures), str(output_path), str(manifest_path))

    def latest(self) -> list[CurrentFixture]:
        return self.load(self.latest_path())

    def latest_path(self) -> Path:
        files = complete_artifact_paths(sorted(self._catalog_dir().glob("*.jsonl"))) if self._catalog_dir().exists() else []
        if not files:
            raise FileNotFoundError("No normalized current fixture catalog exists; run normalize-current-fixtures first")
        return files[-1]

    def load(self, path: Path) -> list[CurrentFixture]:
        verify_artifact(self.root, path)
        return [CurrentFixture(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]

    def latest_with_path(self) -> tuple[Path, list[CurrentFixture]]:
        path = self.latest_path()
        return path, self.load(path)

    def _latest_raw_path(self) -> Path:
        directory = self.root / "raw" / "fpl" / "fixtures"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No FPL fixture snapshot exists; run fetch-fixtures first")
        return files[-1]

    def _catalog_dir(self) -> Path:
        return self.root / "normalized" / "current" / "fixtures"

    def _output_path(self, source_id: str) -> Path:
        return self._catalog_dir() / f"{source_id}.jsonl"


def normalize_fixtures(payload: Any) -> list[CurrentFixture]:
    if not isinstance(payload, list):
        raise ValueError("Fixture payload must be a list")
    fixtures: list[CurrentFixture] = []
    for index, fixture in enumerate(payload):
        if not isinstance(fixture, dict):
            raise ValueError(f"Fixture at index {index} is not an object")
        try:
            event = fixture.get("event")
            fixtures.append(CurrentFixture(
                id=int(fixture["id"]),
                gameweek=int(event) if event is not None else None,
                kickoff_time=fixture.get("kickoff_time"),
                home_team_id=int(fixture["team_h"]),
                away_team_id=int(fixture["team_a"]),
                home_difficulty=int(fixture["team_h_difficulty"]),
                away_difficulty=int(fixture["team_a_difficulty"]),
                finished=bool(fixture["finished"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Fixture at index {index} is missing or has invalid required FPL fields") from exc
    return sorted(fixtures, key=lambda fixture: fixture.id)
