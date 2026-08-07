from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


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


@dataclass(frozen=True)
class CurrentPlayerCatalog:
    source_snapshot: str
    fetched_at: datetime
    players: int
    output_path: str


class CurrentPlayerCatalogStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def normalize_latest(self) -> CurrentPlayerCatalog:
        source_path = self._latest_bootstrap_path()
        document = json.loads(source_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(document["metadata"]["fetched_at"])
        players = normalize_bootstrap_players(document["payload"])
        output_path = self._output_path(source_path.stem)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(json.dumps(asdict(player), sort_keys=True) + "\n" for player in players), encoding="utf-8")
        manifest_path = self._manifest_path(source_path.stem)
        manifest_path.write_text(json.dumps({
            "source_snapshot": str(source_path),
            "fetched_at": fetched_at.isoformat(),
            "players": len(players),
            "output_path": str(output_path),
        }, indent=2, sort_keys=True), encoding="utf-8")
        return CurrentPlayerCatalog(str(source_path), fetched_at, len(players), str(output_path))

    def latest_players(self) -> list[CurrentPlayer]:
        files = sorted(self._catalog_dir().glob("*.jsonl")) if self._catalog_dir().exists() else []
        if not files:
            raise FileNotFoundError("No normalized current-player catalog exists; run normalize-current-players first")
        return [CurrentPlayer(**json.loads(line)) for line in files[-1].read_text(encoding="utf-8").splitlines()]

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
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Player at index {index} is missing or has invalid required FPL fields") from exc
    return sorted(players, key=lambda player: player.id)
