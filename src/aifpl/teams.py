from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CurrentTeam:
    id: int
    name: str
    short_name: str
    code: int
    logo_url: str


class CurrentTeamCatalogStore:
    """Load current FPL teams from the latest snapshot or bundled manifest."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def latest(self) -> list[CurrentTeam]:
        payload = self._latest_snapshot_payload()
        if payload is None:
            payload = self._bundled_payload()
        return normalize_teams(payload)

    def _latest_snapshot_payload(self) -> dict[str, Any] | None:
        directory = self.root / "raw" / "fpl" / "bootstrap"
        documents: list[tuple[datetime, dict[str, Any]]] = []
        for path in directory.glob("*.json") if directory.exists() else []:
            document = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(document["metadata"]["fetched_at"])
            documents.append((fetched_at, document["payload"]))
        if not documents:
            return None
        return max(documents, key=lambda item: item[0])[1]

    @staticmethod
    def _bundled_payload() -> dict[str, Any]:
        path = Path(__file__).with_name("static") / "teams.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        payload = document.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Bundled team manifest is missing its payload")
        return payload


def normalize_teams(payload: dict[str, Any]) -> list[CurrentTeam]:
    teams = payload.get("teams")
    if not isinstance(teams, list):
        raise ValueError("FPL bootstrap payload must contain a team collection")

    normalized: list[CurrentTeam] = []
    for index, team in enumerate(teams):
        if not isinstance(team, dict):
            raise ValueError(f"Team at index {index} is not an object")
        try:
            team_id = int(team["id"])
            name = str(team["name"])
            short_name = str(team["short_name"])
            code = int(team["code"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Team at index {index} is missing required FPL fields") from exc
        normalized.append(CurrentTeam(
            id=team_id,
            name=name,
            short_name=short_name,
            code=code,
            logo_url=f"/teams/{team_id}/logo.png",
        ))
    return sorted(normalized, key=lambda team: team.id)


def team_logo_path(team_id: int) -> Path:
    return Path(__file__).with_name("static") / "team-logos" / f"{team_id}.png"
