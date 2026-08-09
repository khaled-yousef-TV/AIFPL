from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from aifpl.historical import PlayerGameweekRecord
from aifpl.artifacts import json_bytes, jsonl_bytes, sha256_path, write_immutable


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
    data_cutoff: datetime
    source_path: str
    source_manifest_path: str
    manifest_path: str
    warning: str = "Outcome-only baseline; cutoff is fixture event-time, not historical source-publication time."


def aggregate_player_gameweeks(records: Iterable[PlayerGameweekRecord]) -> dict[tuple[int, int], int]:
    """Aggregate player-fixture rows into one FPL total per player and gameweek."""
    totals: dict[tuple[int, int], int] = defaultdict(int)
    for record in records:
        totals[(record.player_id, record.gameweek)] += record.total_points
    return dict(totals)


def _kickoff(record: PlayerGameweekRecord) -> datetime:
    return datetime.fromisoformat(record.kickoff_time.replace("Z", "+00:00"))


def rolling_average_predictions(
    season: str, records: Iterable[PlayerGameweekRecord], start_gameweek: int, end_gameweek: int, window: int
) -> tuple[list[PredictionRecord], int]:
    if window < 1:
        raise ValueError("window must be at least 1")
    if start_gameweek < 1 or end_gameweek < start_gameweek:
        raise ValueError("gameweek range is invalid")
    grouped_records: dict[tuple[int, int], list[PlayerGameweekRecord]] = defaultdict(list)
    for record in records:
        grouped_records[(record.player_id, record.gameweek)].append(record)
    totals = aggregate_player_gameweeks(record for group in grouped_records.values() for record in group)
    players = sorted({player_id for player_id, _ in totals})
    predictions: list[PredictionRecord] = []
    eligible = 0
    for gameweek in range(start_gameweek, end_gameweek + 1):
        for player_id in players:
            actual = totals.get((player_id, gameweek))
            if actual is None:
                continue
            eligible += 1
            target_kickoff = min(_kickoff(record) for record in grouped_records[(player_id, gameweek)])
            history = [
                totals[(player_id, previous)]
                for previous in range(max(1, gameweek - window), gameweek)
                if (player_id, previous) in totals
                and max(_kickoff(record) for record in grouped_records[(player_id, previous)]) < target_kickoff
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
        self, season: str, import_id: str, start_gameweek: int, end_gameweek: int, data_cutoff: datetime, window: int = 5
    ) -> BacktestSummary:
        if data_cutoff.tzinfo is None:
            raise ValueError("data_cutoff must be timezone-aware")
        data_cutoff = data_cutoff.astimezone(timezone.utc)
        source_path = self.root / "normalized" / "historical" / season / import_id / "player_gameweeks.jsonl"
        if not source_path.exists():
            raise FileNotFoundError(f"No normalized data found for season {season}, import {import_id}")
        records = [PlayerGameweekRecord(**json.loads(line)) for line in source_path.read_text(encoding="utf-8").splitlines()]
        grouped_records: dict[tuple[int, int], list[PlayerGameweekRecord]] = defaultdict(list)
        for record in records:
            grouped_records[(record.player_id, record.gameweek)].append(record)
        records = [
            record
            for group in grouped_records.values()
            if all(_kickoff(record) <= data_cutoff for record in group)
            for record in group
        ]
        predictions, eligible = rolling_average_predictions(season, records, start_gameweek, end_gameweek, window)
        if not predictions:
            raise ValueError("No predictions were possible; import earlier gameweeks or choose a later range")
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = self.root / "backtests" / season / run_id / "predictions.jsonl"
        write_immutable(output_path, jsonl_bytes(predictions))
        errors = [row.predicted_points - row.actual_points for row in predictions]
        source_manifest_path = self.root / "normalized" / "historical" / season / "imports" / f"{import_id}.json"
        manifest_path = output_path.parent / "backtest.manifest.json"
        summary = BacktestSummary(
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
            data_cutoff=data_cutoff,
            source_path=str(source_path),
            source_manifest_path=str(source_manifest_path),
            manifest_path=str(manifest_path),
        )
        manifest = {
            "schema_version": 1, "artifact_type": "baseline_backtest", "created_at": created_at.isoformat(),
            "artifact_path": str(output_path), "artifact_sha256": sha256_path(output_path),
            "parameters": {"season": season, "start_gameweek": start_gameweek, "end_gameweek": end_gameweek,
                           "window": window, "data_cutoff": data_cutoff.isoformat()},
            "sources": [{"role": "historical_player_gameweeks", "path": str(source_path), "sha256": sha256_path(source_path)},
                        {"role": "historical_import_manifest", "path": str(source_manifest_path),
                         "sha256": sha256_path(source_manifest_path) if source_manifest_path.exists() else None}],
            "metrics": {"predictions": summary.predictions, "eligible_player_gameweeks": summary.eligible_player_gameweeks,
                        "coverage": summary.coverage, "mae": summary.mae, "rmse": summary.rmse, "bias": summary.bias},
            "warning": summary.warning,
        }
        write_immutable(manifest_path, json_bytes(manifest, pretty=True))
        return summary
