from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.artifacts import ArtifactIntegrityError, json_bytes, sha256_path, write_immutable
from aifpl.projections import PredictionRecord


@dataclass(frozen=True)
class ErrorMetrics:
    observations: int
    mae: float
    rmse: float
    bias: float


@dataclass(frozen=True)
class CalibrationReport:
    source_predictions: str
    train_gameweeks: list[int]
    evaluation_gameweeks: list[int]
    slope: float
    intercept: float
    raw_metrics: ErrorMetrics
    calibrated_metrics: ErrorMetrics
    output_path: str
    created_at: datetime
    warning: str = "Calibration applies only to this archived forecast methodology and population."


def fit_walk_forward_calibration(
    root: Path, prediction_path: Path, train_end_gameweek: int, evaluation_start_gameweek: int,
) -> CalibrationReport:
    _verify_archive(root, prediction_path)
    rows = [PredictionRecord(**json.loads(line)) for line in prediction_path.read_text(encoding="utf-8").splitlines()]
    train = [row for row in rows if row.gameweek <= train_end_gameweek]
    evaluation = [row for row in rows if row.gameweek >= evaluation_start_gameweek]
    if train_end_gameweek >= evaluation_start_gameweek:
        raise ValueError("Training gameweeks must end before evaluation gameweeks begin")
    if not train or not evaluation:
        raise ValueError("Calibration requires non-empty chronological training and evaluation populations")
    mean_x = sum(row.predicted_points for row in train) / len(train)
    mean_y = sum(row.actual_points for row in train) / len(train)
    denominator = sum((row.predicted_points - mean_x) ** 2 for row in train)
    slope = (
        sum((row.predicted_points - mean_x) * (row.actual_points - mean_y) for row in train) / denominator
        if denominator else 1.0
    )
    intercept = mean_y - slope * mean_x
    raw_pairs = [(row.predicted_points, row.actual_points) for row in evaluation]
    calibrated_pairs = [(intercept + slope * row.predicted_points, row.actual_points) for row in evaluation]
    created_at = datetime.now(timezone.utc)
    output_path = root / "calibration" / prediction_path.parent.parent.name / f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    report = CalibrationReport(
        source_predictions=str(prediction_path),
        train_gameweeks=sorted({row.gameweek for row in train}),
        evaluation_gameweeks=sorted({row.gameweek for row in evaluation}),
        slope=round(slope, 6), intercept=round(intercept, 6),
        raw_metrics=_metrics(raw_pairs), calibrated_metrics=_metrics(calibrated_pairs),
        output_path=str(output_path), created_at=created_at,
    )
    document = asdict(report)
    document["source_sha256"] = sha256_path(prediction_path)
    write_immutable(output_path, json_bytes(document, pretty=True))
    return report


def compare_prediction_runs(root: Path, paths: list[Path]) -> dict[str, ErrorMetrics]:
    if len(paths) < 2:
        raise ValueError("At least two prediction runs are required for comparison")
    runs = {}
    for path in paths:
        _verify_archive(root, path)
        rows = _load(path)
        keyed = {(row.season, row.gameweek, row.player_id): row for row in rows}
        if len(keyed) != len(rows):
            raise ValueError(f"Prediction run contains duplicate rows: {path}")
        runs[str(path)] = keyed
    common = set.intersection(*(set(rows) for rows in runs.values()))
    if not common:
        raise ValueError("Prediction runs have no common player-gameweek population")
    return {
        path: _metrics([(rows[key].predicted_points, rows[key].actual_points) for key in sorted(common)])
        for path, rows in runs.items()
    }


def _load(path: Path) -> list[PredictionRecord]:
    return [PredictionRecord(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]


def _metrics(pairs: list[tuple[float, int]]) -> ErrorMetrics:
    errors = [predicted - actual for predicted, actual in pairs]
    return ErrorMetrics(
        observations=len(errors),
        mae=round(sum(abs(error) for error in errors) / len(errors), 4),
        rmse=round(math.sqrt(sum(error**2 for error in errors) / len(errors)), 4),
        bias=round(sum(errors) / len(errors), 4),
    )


def _verify_archive(root: Path, prediction_path: Path) -> None:
    if not prediction_path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Prediction archive must be below the configured data root")
    manifest_path = prediction_path.parent / "backtest.manifest.json"
    if not manifest_path.exists():
        raise ArtifactIntegrityError(f"Prediction archive has no backtest manifest: {prediction_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("artifact_sha256") != sha256_path(prediction_path):
        raise ArtifactIntegrityError(f"Prediction archive hash mismatch: {prediction_path}")
