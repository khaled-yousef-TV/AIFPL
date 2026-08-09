from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from aifpl.artifacts import complete_artifact_paths, jsonl_bytes, verify_artifact, write_immutable, write_manifest


Position = Literal["GK", "DEF", "MID", "FWD"]
POSITION_MAP: dict[int, Position] = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


@dataclass(frozen=True)
class CurrentPlayer:
    id: int
    name: str
    position: Position
    club_id: int
    club: str
    cost: int
    status: str
    chance_of_playing_next_round: int | None
    form: float
    points_per_game: float
    total_points: int
    minutes: int
    starts: int
    expected_goals: float
    expected_assists: float
    expected_goal_involvements: float
    expected_goals_conceded: float
    first_name: str = ""
    second_name: str = ""
    news: str = ""
    news_added: str | None = None
    selected_by_percent: float = 0.0
    ep_next: float = 0.0


@dataclass(frozen=True)
class CurrentPlayerCatalog:
    source_snapshot: str
    fetched_at: datetime
    players: int
    output_path: str
    manifest_path: str | None = None


class CurrentPlayerCatalogStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def normalize_latest(self) -> CurrentPlayerCatalog:
        return self.normalize(self._latest_bootstrap_path())

    def normalize(self, source_path: Path) -> CurrentPlayerCatalog:
        document = json.loads(source_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(document["metadata"]["fetched_at"])
        players = normalize_bootstrap_players(document["payload"])
        output_path = self._output_path(source_path.stem)
        if output_path.exists():
            if self.load(output_path) != players:
                raise ValueError(f"Existing current-player catalog disagrees with source snapshot: {output_path}")
        else:
            write_immutable(output_path, jsonl_bytes(players))
        manifest_path = self._manifest_path(source_path.stem)
        if not manifest_path.exists():
            manifest_path = write_manifest(
                self.root, output_path, artifact_type="current_players", created_at=fetched_at.isoformat(),
                record_count=len(players), sources={"bootstrap_snapshot": source_path},
            )
        return CurrentPlayerCatalog(str(source_path), fetched_at, len(players), str(output_path), str(manifest_path))

    def latest_players(self) -> list[CurrentPlayer]:
        return self.load(self.latest_path())

    def latest_path(self) -> Path:
        files = complete_artifact_paths(sorted(self._catalog_dir().glob("*.jsonl"))) if self._catalog_dir().exists() else []
        if not files:
            raise FileNotFoundError("No normalized current-player catalog exists; run normalize-current-players first")
        return files[-1]

    def load(self, path: Path) -> list[CurrentPlayer]:
        verify_artifact(self.root, path)
        return [CurrentPlayer(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]

    def latest_with_path(self) -> tuple[Path, list[CurrentPlayer]]:
        path = self.latest_path()
        return path, self.load(path)

    def _latest_bootstrap_path(self) -> Path:
        directory = self.root / "raw" / "fpl" / "bootstrap"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No FPL bootstrap snapshot exists; run fetch-bootstrap first")
        return files[-1]

    def _catalog_dir(self) -> Path:
        return self.root / "normalized" / "current" / "players"

    def _output_path(self, source_id: str) -> Path:
        return self._catalog_dir() / f"{source_id}.jsonl"

    def _manifest_path(self, source_id: str) -> Path:
        return self._catalog_dir() / f"{source_id}.manifest.json"


def normalize_bootstrap_players(payload: dict[str, Any]) -> list[CurrentPlayer]:
    teams = payload.get("teams")
    elements = payload.get("elements")
    if not isinstance(teams, list) or not isinstance(elements, list):
        raise ValueError("Bootstrap payload must contain team and player collections")
    club_names = {team.get("id"): team.get("name") for team in teams if isinstance(team, dict)}
    players: list[CurrentPlayer] = []
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"Player at index {index} is not an object")
        try:
            position = POSITION_MAP[element["element_type"]]
            club_id = int(element["team"])
            club = club_names[club_id]
            if not isinstance(club, str):
                raise KeyError("team name")
            chance = element.get("chance_of_playing_next_round")
            players.append(CurrentPlayer(
                id=int(element["id"]),
                name=str(element["web_name"]),
                position=position,
                club_id=club_id,
                club=club,
                cost=int(element["now_cost"]),
                status=str(element["status"]),
                chance_of_playing_next_round=int(chance) if chance is not None else None,
                form=float(element["form"]),
                points_per_game=float(element["points_per_game"]),
                total_points=int(element["total_points"]),
                minutes=int(element["minutes"]),
                starts=int(element["starts"]),
                expected_goals=float(element["expected_goals"]),
                expected_assists=float(element["expected_assists"]),
                expected_goal_involvements=float(element["expected_goal_involvements"]),
                expected_goals_conceded=float(element["expected_goals_conceded"]),
                first_name=str(element.get("first_name", "")),
                second_name=str(element.get("second_name", "")),
                news=str(element.get("news", "")),
                news_added=str(element["news_added"]) if element.get("news_added") else None,
                selected_by_percent=float(element.get("selected_by_percent", 0)),
                ep_next=float(element.get("ep_next") or 0),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Player at index {index} is missing or has invalid required FPL fields") from exc
    return sorted(players, key=lambda player: player.id)
