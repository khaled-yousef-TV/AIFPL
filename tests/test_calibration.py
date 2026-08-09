import json

import pytest

from aifpl.artifacts import json_bytes, sha256_path, write_immutable
from aifpl.calibration import compare_prediction_runs, fit_walk_forward_calibration


def predictions(path, offset: float = 0) -> None:
    rows = [
        {"season": "2025-26", "gameweek": gameweek, "player_id": player,
         "predicted_points": float(gameweek + player + offset), "actual_points": gameweek + player + 1,
         "history_gameweeks": gameweek - 1}
        for gameweek in (2, 3, 4) for player in (1, 2)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest = {"artifact_sha256": sha256_path(path)}
    write_immutable(path.parent / "backtest.manifest.json", json_bytes(manifest, pretty=True))


def test_calibration_uses_earlier_training_and_later_evaluation(tmp_path) -> None:
    source = tmp_path / "backtests" / "season" / "run" / "predictions.jsonl"
    predictions(source)

    report = fit_walk_forward_calibration(tmp_path, source, 3, 4)

    assert report.train_gameweeks == [2, 3]
    assert report.evaluation_gameweeks == [4]
    assert report.calibrated_metrics.mae == 0


def test_calibration_rejects_overlapping_train_and_evaluation(tmp_path) -> None:
    source = tmp_path / "predictions.jsonl"
    predictions(source)

    with pytest.raises(ValueError, match="before"):
        fit_walk_forward_calibration(tmp_path, source, 3, 3)


def test_model_comparison_uses_common_population(tmp_path) -> None:
    first, second = tmp_path / "a" / "predictions.jsonl", tmp_path / "b" / "predictions.jsonl"
    predictions(first)
    predictions(second, 1)

    comparison = compare_prediction_runs(tmp_path, [first, second])

    assert comparison[str(first)].observations == comparison[str(second)].observations == 6
