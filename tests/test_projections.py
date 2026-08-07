from datetime import datetime, timezone

import pytest

from aifpl.historical import PlayerGameweekRecord
from aifpl.projections import aggregate_player_gameweeks, rolling_average_predictions


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
    records = [record(1, 1, 2, 1), record(1, 2, 100, 2)]

    predictions, eligible = rolling_average_predictions("2025-26", records, 2, 2, window=5)

    assert eligible == 1
    assert predictions[0].predicted_points == 2
    assert predictions[0].actual_points == 100


def test_rolling_prediction_requires_positive_window() -> None:
    with pytest.raises(ValueError, match="window"):
        rolling_average_predictions("2025-26", [], 1, 1, window=0)
