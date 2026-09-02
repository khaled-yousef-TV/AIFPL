from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from aifpl.current import CurrentPlayer
from aifpl.historical import PlayerGameweekRecord

NEW_SIGNING_MINUTES_MULTIPLIER = 0.85
NEW_CONTEXT_STAT_DECAY = 0.85


@dataclass(frozen=True)
class TransferProfile:
    player_id: int
    previous_club: str | None
    is_new_signing: bool
    minutes_multiplier: float
    prior_goals_per_90: float
    prior_assists_per_90: float
    prior_points_per_90: float
    club_history: tuple[str, ...] = ()

    @property
    def has_prior_stats(self) -> bool:
        return self.prior_goals_per_90 > 0 or self.prior_assists_per_90 > 0 or self.prior_points_per_90 > 0

    @property
    def previous_clubs(self) -> tuple[str, ...]:
        """Compatibility name for consumers that call the history a club list."""
        return self.club_history


class TransferAwarenessStore:
    """Detect players who changed clubs since the last completed season.

    The previous season's club comes from the most recent imported historical
    season (vaastav CSV source). The current club comes from the live FPL
    bootstrap. A mismatch marks the player as a new signing, which scales
    expected minutes down slightly to reflect settling-in and selection risk.
    Prior per-90 goal/assist/points rates are also exposed so projection
    blends have a floor before the current season accumulates minutes.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def latest(self, players: list[CurrentPlayer]) -> dict[int, TransferProfile]:
        previous = self._previous_season_rows()
        profiles: dict[int, TransferProfile] = {}
        for player in players:
            last = self._lookup(previous, player)
            if last is None:
                profiles[player.id] = TransferProfile(
                    player_id=player.id, previous_club=None, is_new_signing=False,
                    minutes_multiplier=1.0, prior_goals_per_90=0.0,
                    prior_assists_per_90=0.0, prior_points_per_90=0.0, club_history=(),
                )
                continue
            new_signing = normalize_club_name(last.club) != normalize_club_name(player.club)
            minutes_multiplier = NEW_SIGNING_MINUTES_MULTIPLIER if new_signing else 1.0
            profiles[player.id] = TransferProfile(
                player_id=player.id,
                previous_club=last.club,
                is_new_signing=new_signing,
                minutes_multiplier=minutes_multiplier,
                prior_goals_per_90=_rate(last.goals_scored, last.minutes),
                prior_assists_per_90=_rate(last.assists, last.minutes),
                prior_points_per_90=_rate(last.total_points, last.minutes),
                club_history=last.club_history,
            )
        return profiles

    @staticmethod
    def _lookup(previous: dict[str, _LastSeasonRow], player: CurrentPlayer) -> _LastSeasonRow | None:
        full_name = f"{player.first_name} {player.second_name}".strip() if player.first_name else player.name
        for key in (f"id:{player.id}", normalize_name(full_name), normalize_name(player.name)):
            if key in previous:
                return previous[key]
        return None

    def _previous_season_rows(self) -> dict[str, _LastSeasonRow]:
        latest_path = self._latest_normalized_path()
        if latest_path is None:
            return {}
        records = [
            PlayerGameweekRecord(**json.loads(line))
            for line in latest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        # A player can have multiple rows in one GW (a double gameweek), and
        # can also change clubs during the imported season.  Aggregate by the
        # stable source ID while retaining the last chronological club.
        aggregates: dict[int, _LastSeasonRow] = {}
        names_by_id: dict[int, set[str]] = {}
        for record in sorted(records, key=_record_sort_key):
            names_by_id.setdefault(record.player_id, set()).add(record.player_name)
            current = aggregates.get(record.player_id)
            if current is None:
                aggregates[record.player_id] = _LastSeasonRow(
                    player_id=record.player_id,
                    player_name=record.player_name,
                    gameweek=record.gameweek,
                    club=record.team,
                    minutes=record.minutes,
                    goals_scored=record.goals_scored,
                    assists=record.assists,
                    total_points=record.total_points,
                    club_history=(record.team,),
                )
                continue
            history = current.club_history
            if not any(normalize_club_name(record.team) == normalize_club_name(club) for club in history):
                history = (*history, record.team)
            aggregates[record.player_id] = _LastSeasonRow(
                player_id=current.player_id,
                player_name=record.player_name or current.player_name,
                gameweek=record.gameweek,
                club=record.team,
                minutes=current.minutes + record.minutes,
                goals_scored=current.goals_scored + record.goals_scored,
                assists=current.assists + record.assists,
                total_points=current.total_points + record.total_points,
                club_history=history,
            )

        rows: dict[str, _LastSeasonRow] = {}
        for aggregate in aggregates.values():
            rows[f"id:{aggregate.player_id}"] = aggregate
            for player_name in names_by_id.get(aggregate.player_id, {aggregate.player_name}):
                rows.setdefault(normalize_name(player_name), aggregate)
        return rows

    def _latest_normalized_path(self) -> Path | None:
        candidates: list[tuple[int, str, Path]] = []
        imports_dir = self.root / "normalized" / "historical"
        if not imports_dir.exists():
            return None
        for season_dir in imports_dir.iterdir():
            if not season_dir.is_dir() or len(season_dir.name) != 7 or season_dir.name[4] != "-":
                continue
            try:
                start_year = int(season_dir.name[:4])
            except ValueError:
                continue
            for import_id in (season_dir / "imports").glob("*.json"):
                try:
                    document = json.loads(import_id.read_text(encoding="utf-8"))
                    normalized = Path(document["normalized_path"])
                except (KeyError, ValueError, OSError, json.JSONDecodeError):
                    continue
                if normalized.is_file():
                    candidates.append((start_year, document.get("imported_at", ""), normalized))
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], item[1]))[2]


@dataclass(frozen=True)
class _LastSeasonRow:
    player_id: int
    player_name: str
    gameweek: int
    club: str
    minutes: int
    goals_scored: int
    assists: int
    total_points: int
    club_history: tuple[str, ...]


def _record_sort_key(record: PlayerGameweekRecord) -> tuple[int, str, int]:
    return record.gameweek, str(record.kickoff_time or ""), record.fixture_id


def _rate(value: int, minutes: int) -> float:
    if minutes <= 0:
        return 0.0
    return round(value / minutes * 90, 4)


def normalize_club_name(name: str) -> str:
    return " ".join(name.casefold().replace(".", " ").replace("'", "").split())


def normalize_name(name: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return " ".join(folded.casefold().replace(".", " ").split())
