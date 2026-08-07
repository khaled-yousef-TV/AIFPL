from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from aifpl.historical import PlayerGameweekRecord


@dataclass(frozen=True)
class PredictionRecord:
    season: str
    gameweek: int
    player_id: int
    predicted_points: float
    actual_points: int
    history_gameweeks: int


@dataclass(frozen=True)
class BacktestSummary:
    season: str
    source_import_id: str
    window: int
    gameweeks: list[int]
    predictions: int
    eligible_player_gameweeks: int
    coverage: float
    mae: float
    rmse: float
    bias: float
    output_path: str
    created_at: datetime
    warning: str = "Outcome-only baseline; it does not represent pre-deadline information."


def aggregate_player_gameweeks(records: Iterable[PlayerGameweekRecord]) -> dict[tuple[int, int], int]:
    """Aggregate player-fixture rows into one FPL total per player and gameweek."""
    totals: dict[tuple[int, int], int] = defaultdict(int)
    for record in records:
        totals[(record.player_id, record.gameweek)] += record.total_points
    return dict(totals)


def rolling_average_predictions(
    season: str, records: Iterable[PlayerGameweekRecord], start_gameweek: int, end_gameweek: int, window: int
) -> tuple[list[PredictionRecord], int]:
    if window < 1:
        raise ValueError("window must be at least 1")
    if start_gameweek < 1 or end_gameweek < start_gameweek:
        raise ValueError("gameweek range is invalid")
    totals = aggregate_player_gameweeks(records)
    players = sorted({player_id for player_id, _ in totals})
    predictions: list[PredictionRecord] = []
    eligible = 0
    for gameweek in range(start_gameweek, end_gameweek + 1):
        for player_id in players:
            actual = totals.get((player_id, gameweek))
            if actual is None:
                continue
            eligible += 1
            history = [
                totals[(player_id, previous)]
                for previous in range(max(1, gameweek - window), gameweek)
                if (player_id, previous) in totals
            ]
            if not history:
                continue
            predictions.append(PredictionRecord(
                season=season,
                gameweek=gameweek,
                player_id=player_id,
                predicted_points=round(sum(history) / len(history), 4),
                actual_points=actual,
                history_gameweeks=len(history),
            ))
    return predictions, eligible


class BaselineBacktester:
    def __init__(self, root: Path) -> None:
        self.root = root

    def run(
        self, season: str, import_id: str, start_gameweek: int, end_gameweek: int, window: int = 5
    ) -> BacktestSummary:
        source_path = self.root / "normalized" / "historical" / season / import_id / "player_gameweeks.jsonl"
        if not source_path.exists():
            raise FileNotFoundError(f"No normalized data found for season {season}, import {import_id}")
        records = [PlayerGameweekRecord(**json.loads(line)) for line in source_path.read_text(encoding="utf-8").splitlines()]
        predictions, eligible = rolling_average_predictions(season, records, start_gameweek, end_gameweek, window)
        if not predictions:
            raise ValueError("No predictions were possible; import earlier gameweeks or choose a later range")
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = self.root / "backtests" / season / run_id / "predictions.jsonl"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in predictions), encoding="utf-8")
        errors = [row.predicted_points - row.actual_points for row in predictions]
        return BacktestSummary(
            season=season,
            source_import_id=import_id,
            window=window,
            gameweeks=list(range(start_gameweek, end_gameweek + 1)),
            predictions=len(predictions),
            eligible_player_gameweeks=eligible,
            coverage=round(len(predictions) / eligible, 4) if eligible else 0.0,
            mae=round(sum(abs(error) for error in errors) / len(errors), 4),
            rmse=round(math.sqrt(sum(error**2 for error in errors) / len(errors)), 4),
            bias=round(sum(errors) / len(errors), 4),
            output_path=str(output_path),
            created_at=created_at,
        )
