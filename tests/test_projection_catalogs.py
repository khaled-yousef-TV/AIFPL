import pytest

from aifpl.fixture_projections import FixtureGameweekProjection
from aifpl.projection_catalogs import _aggregate


def row(gameweek: int, points: float) -> FixtureGameweekProjection:
    return FixtureGameweekProjection(1, "Player", "MID", "Arsenal", 75, gameweek, 1, 3.0, points)


def test_projection_catalog_aggregates_gameweek_rows_for_optimization() -> None:
    candidate = _aggregate([row(1, 4.5), row(2, 5.5)])[0]

    assert candidate.player_id == 1
    assert candidate.projected_points == 10


def test_projection_catalog_rejects_duplicate_player_gameweek() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        _aggregate([row(1, 4), row(1, 5)])


def test_projection_catalog_rejects_incomplete_player_horizon() -> None:
    first = row(1, 4)
    second_player = FixtureGameweekProjection(2, "Other", "MID", "Chelsea", 75, 1, 1, 3.0, 4)
    second_week = FixtureGameweekProjection(1, "Player", "MID", "Arsenal", 75, 2, 1, 3.0, 5)

    with pytest.raises(ValueError, match="Incomplete"):
        _aggregate([first, second_week, second_player])
