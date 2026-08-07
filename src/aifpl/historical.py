from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx


HISTORICAL_SOURCE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


class HistoricalSourceError(RuntimeError):
    """Raised when a historical result source cannot be downloaded or validated."""


@dataclass(frozen=True)
class PlayerGameweekRecord:
    season: str
    gameweek: int
    player_id: int
    player_name: str
    position: str
    team: str
    kickoff_time: str
    fixture_id: int
    opponent_team_id: int
    was_home: bool
    minutes: int
    total_points: int
    goals_scored: int
    assists: int
    clean_sheets: int
    saves: int
    bonus: int
    value: int


@dataclass(frozen=True)
class SeasonImportSummary:
    season: str
    import_id: str
    gameweeks: list[int]
    records: int
    raw_files: int
    normalized_path: str
    imported_at: datetime


class HistoricalSeasonImporter:
    """Import completed gameweek outcomes while retaining source provenance."""

    def __init__(self, root: Path, source_base_url: str = HISTORICAL_SOURCE, timeout_seconds: float = 30.0) -> None:
        self.root = root
        self.source_base_url = source_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def import_season(self, season: str, start_gameweek: int = 1, end_gameweek: int = 38) -> SeasonImportSummary:
        self._validate_request(season, start_gameweek, end_gameweek)
        imported_at = datetime.now(timezone.utc)
        import_id = imported_at.strftime("%Y%m%dT%H%M%S%fZ")
        records: list[PlayerGameweekRecord] = []
        raw_files: list[dict[str, str | int]] = []
        for gameweek in range(start_gameweek, end_gameweek + 1):
            url = f"{self.source_base_url}/{season}/gws/gw{gameweek}.csv"
            content = self._download(url)
            records.extend(parse_gameweek_csv(season, gameweek, content))
            raw_path = self._raw_dir(season, import_id) / f"gw{gameweek}.csv"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(content, encoding="utf-8")
            raw_files.append({"gameweek": gameweek, "url": url, "sha256": _sha256(content)})

        normalized_path = self._normalized_path(season, import_id)
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_path.write_text("".join(json.dumps(asdict(record), sort_keys=True) + "\n" for record in records), encoding="utf-8")
        manifest = {
            "source": "community:vaastav/Fantasy-Premier-League",
            "source_base_url": self.source_base_url,
            "season": season,
            "imported_at": imported_at.isoformat(),
            "gameweeks": list(range(start_gameweek, end_gameweek + 1)),
            "raw_files": raw_files,
            "normalized_path": str(normalized_path),
            "record_count": len(records),
            "warning": "Outcome data only; this is not a historical pre-deadline snapshot.",
        }
        self._manifest_path(season, import_id).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return SeasonImportSummary(
            season=season,
            import_id=import_id,
            gameweeks=manifest["gameweeks"],
            records=len(records),
            raw_files=len(raw_files),
            normalized_path=str(normalized_path),
            imported_at=imported_at,
        )

    def summary(self, season: str) -> SeasonImportSummary:
        directory = self._manifest_dir(season)
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise FileNotFoundError(f"No imported historical season found: {season}")
        path = files[-1]
        manifest = json.loads(path.read_text(encoding="utf-8"))
        return SeasonImportSummary(
            season=manifest["season"],
            import_id=path.stem,
            gameweeks=manifest["gameweeks"],
            records=manifest["record_count"],
            raw_files=len(manifest["raw_files"]),
            normalized_path=manifest["normalized_path"],
            imported_at=datetime.fromisoformat(manifest["imported_at"]),
        )

    def latest_import_id(self, season: str) -> str:
        return self.summary(season).import_id

    def _download(self, url: str) -> str:
        try:
            response = httpx.get(url, timeout=self.timeout_seconds, headers={"User-Agent": "aifpl-backtester/0.1"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HistoricalSourceError(f"Could not download historical source {url}: {exc}") from exc
        return response.text

    @staticmethod
    def _validate_request(season: str, start_gameweek: int, end_gameweek: int) -> None:
        if len(season) != 7 or season[4] != "-" or not season[:4].isdigit() or not season[5:].isdigit():
            raise ValueError("season must have the format YYYY-YY, for example 2025-26")
        if start_gameweek < 1 or end_gameweek > 38 or start_gameweek > end_gameweek:
            raise ValueError("gameweeks must be within 1..38 and start must not exceed end")

    def _raw_dir(self, season: str, import_id: str) -> Path:
        return self.root / "raw" / "historical" / "vaastav" / season / import_id / "gws"

    def _normalized_path(self, season: str, import_id: str) -> Path:
        return self.root / "normalized" / "historical" / season / import_id / "player_gameweeks.jsonl"

    def _manifest_dir(self, season: str) -> Path:
        return self.root / "normalized" / "historical" / season / "imports"

    def _manifest_path(self, season: str, import_id: str) -> Path:
        directory = self._manifest_dir(season)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{import_id}.json"


def parse_gameweek_csv(season: str, gameweek: int, content: str) -> list[PlayerGameweekRecord]:
    reader = csv.DictReader(io.StringIO(content))
    required = {
        "element", "name", "position", "team", "kickoff_time", "fixture", "opponent_team", "was_home",
        "minutes", "total_points", "goals_scored", "assists", "clean_sheets", "saves", "bonus", "value",
    }
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise HistoricalSourceError("Historical gameweek CSV is missing required FPL result columns")
    records: list[PlayerGameweekRecord] = []
    for row_number, row in enumerate(reader, start=2):
        try:
            records.append(PlayerGameweekRecord(
                season=season,
                gameweek=gameweek,
                player_id=int(row["element"]),
                player_name=row["name"],
                position=row["position"],
                team=row["team"],
                kickoff_time=row["kickoff_time"],
                fixture_id=int(row["fixture"]),
                opponent_team_id=int(row["opponent_team"]),
                was_home=_parse_bool(row["was_home"]),
                minutes=int(row["minutes"]),
                total_points=int(row["total_points"]),
                goals_scored=int(row["goals_scored"]),
                assists=int(row["assists"]),
                clean_sheets=int(row["clean_sheets"]),
                saves=int(row["saves"]),
                bonus=int(row["bonus"]),
                value=int(row["value"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise HistoricalSourceError(f"Invalid row {row_number} in historical gameweek CSV") from exc
    return records


def _parse_bool(value: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"Expected True or False, received {value!r}")


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
