from aifpl.current import CurrentPlayer
from aifpl.fixture_projections import build_fixture_gameweek_projections
from aifpl.fixtures import CurrentFixture


def player() -> CurrentPlayer:
    return CurrentPlayer(
        id=1, name="Test", position="MID", club_id=1, club="Arsenal", cost=100, status="a",
        chance_of_playing_next_round=None, form=0, points_per_game=5, total_points=100,
        minutes=2000, starts=25, expected_goals=5, expected_assists=4, expected_goal_involvements=9, expected_goals_conceded=20,
    )


def fixture(identifier: int, gameweek: int, difficulty: int) -> CurrentFixture:
    return CurrentFixture(identifier, gameweek, "2026-08-15T14:00:00Z", 1, 2, difficulty, 3, False)


def test_fixture_projections_apply_difficulty_and_double_gameweek_sum() -> None:
    projections = build_fixture_gameweek_projections([player()], [fixture(1, 1, 1), fixture(2, 1, 5)], 1, 2)

    gw1, gw2 = projections
    assert gw1.fixture_count == 2
    assert gw1.average_difficulty == 3.0
    assert gw1.projected_points == 10.0
    assert gw2.fixture_count == 0
    assert gw2.projected_points == 0


def test_fixture_projections_reject_invalid_range() -> None:
    try:
        build_fixture_gameweek_projections([player()], [], 2, 1)
    except ValueError as exc:
        assert "range" in str(exc)
    else:
        raise AssertionError("Expected invalid range error")
