from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


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


class CurrentFixtureCatalogStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def normalize_latest(self) -> FixtureCatalog:
        source_path = self._latest_raw_path()
        document = json.loads(source_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(document["metadata"]["fetched_at"])
        fixtures = normalize_fixtures(document["payload"])
        output_path = self._output_path(source_path.stem)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(json.dumps(asdict(fixture), sort_keys=True) + "\n" for fixture in fixtures), encoding="utf-8")
        return FixtureCatalog(str(source_path), fetched_at, len(fixtures), str(output_path))

    def latest(self) -> list[CurrentFixture]:
        files = sorted(self._catalog_dir().glob("*.jsonl")) if self._catalog_dir().exists() else []
        if not files:
            raise FileNotFoundError("No normalized current fixture catalog exists; run normalize-current-fixtures first")
        return [CurrentFixture(**json.loads(line)) for line in files[-1].read_text(encoding="utf-8").splitlines()]

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
