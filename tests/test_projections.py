import json
from dataclasses import asdict, replace
from datetime import datetime, timezone

import pytest

from aifpl.historical import PlayerGameweekRecord
from aifpl.projections import BaselineBacktester, aggregate_player_gameweeks, rolling_average_predictions


def record(player_id: int, gameweek: int, points: int, fixture_id: int) -> PlayerGameweekRecord:
    return PlayerGameweekRecord(
        season="2025-26", gameweek=gameweek, player_id=player_id, player_name="Test", position="MID", team="Test FC",
        kickoff_time="2025-08-15T19:00:00Z", fixture_id=fixture_id, opponent_team_id=2, was_home=True,
        minutes=90, total_points=points, goals_scored=0, assists=0, clean_sheets=0, saves=0, bonus=0, value=50,
    )


def test_aggregation_handles_double_gameweek_player_rows() -> None:
    totals = aggregate_player_gameweeks([record(1, 2, 4, 10), record(1, 2, 7, 11)])

    assert totals == {(1, 2): 11}


def test_rolling_prediction_does_not_read_the_target_gameweek() -> None:
    records = [
        replace(record(1, 1, 2, 1), kickoff_time="2025-08-15T19:00:00Z"),
        replace(record(1, 2, 100, 2), kickoff_time="2025-08-22T19:00:00Z"),
    ]

    predictions, eligible = rolling_average_predictions("2025-26", records, 2, 2, window=5)

    assert eligible == 1
    assert predictions[0].predicted_points == 2
    assert predictions[0].actual_points == 100


def test_rolling_prediction_excludes_postponed_earlier_gameweek() -> None:
    records = [
        replace(record(1, 1, 20, 1), kickoff_time="2025-08-29T19:00:00Z"),
        replace(record(1, 2, 8, 2), kickoff_time="2025-08-22T19:00:00Z"),
    ]

    predictions, eligible = rolling_average_predictions("2025-26", records, 2, 2, window=5)

    assert eligible == 1
    assert predictions == []


def test_rolling_prediction_requires_positive_window() -> None:
    with pytest.raises(ValueError, match="window"):
        rolling_average_predictions("2025-26", [], 1, 1, window=0)


def test_backtest_persists_explicit_event_time_cutoff_and_provenance(tmp_path) -> None:
    import_id = "import-1"
    source = tmp_path / "normalized" / "historical" / "2025-26" / import_id / "player_gameweeks.jsonl"
    source.parent.mkdir(parents=True)
    rows = [
        replace(record(1, 1, 2, 1), kickoff_time="2025-08-15T19:00:00Z"),
        replace(record(1, 2, 8, 2), kickoff_time="2025-08-22T19:00:00Z"),
    ]
    source.write_text("".join(json.dumps(asdict(row)) + "\n" for row in rows), encoding="utf-8")
    cutoff = datetime(2025, 8, 22, 19, 0, tzinfo=timezone.utc)

    summary = BaselineBacktester(tmp_path).run("2025-26", import_id, 2, 2, cutoff)
    manifest = json.loads((tmp_path / summary.manifest_path).read_text(encoding="utf-8"))

    assert summary.data_cutoff == cutoff
    assert manifest["parameters"]["data_cutoff"] == cutoff.isoformat()
    assert manifest["sources"][0]["sha256"]


def test_backtest_excludes_partial_double_gameweek_at_cutoff(tmp_path) -> None:
    import_id = "import-1"
    source = tmp_path / "normalized" / "historical" / "2025-26" / import_id / "player_gameweeks.jsonl"
    source.parent.mkdir(parents=True)
    rows = [
        replace(record(1, 1, 2, 1), kickoff_time="2025-08-15T19:00:00Z"),
        replace(record(1, 2, 4, 2), kickoff_time="2025-08-22T19:00:00Z"),
        replace(record(1, 2, 7, 3), kickoff_time="2025-08-24T19:00:00Z"),
        replace(record(2, 1, 3, 4), kickoff_time="2025-08-15T19:00:00Z"),
        replace(record(2, 2, 8, 5), kickoff_time="2025-08-22T19:00:00Z"),
    ]
    source.write_text("".join(json.dumps(asdict(row)) + "\n" for row in rows), encoding="utf-8")
    cutoff = datetime(2025, 8, 22, 19, 0, tzinfo=timezone.utc)

    summary = BaselineBacktester(tmp_path).run("2025-26", import_id, 2, 2, cutoff)
    predictions = [json.loads(line) for line in (tmp_path / summary.output_path).read_text().splitlines()]

    assert summary.eligible_player_gameweeks == 1
    assert [(prediction["player_id"], prediction["actual_points"]) for prediction in predictions] == [(2, 8)]


def test_backtest_rejects_naive_cutoff(tmp_path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        BaselineBacktester(tmp_path).run("2025-26", "missing", 2, 2, datetime(2025, 8, 22))
