from dataclasses import replace

from aifpl.current import CurrentPlayer
from aifpl.xg_projections import xg_xa_blend


def player() -> CurrentPlayer:
    return CurrentPlayer(
        id=1, name="Test", position="MID", club_id=1, club="Arsenal", cost=100, status="a",
        chance_of_playing_next_round=None, form=0, points_per_game=5, total_points=100,
        minutes=900, starts=10, expected_goals=4, expected_assists=3, expected_goal_involvements=7, expected_goals_conceded=8,
    )


def test_xg_xa_blend_derives_per_90_and_expected_minutes() -> None:
    projection = xg_xa_blend(player())

    assert projection.expected_minutes == 23.6842
    assert projection.xg_per_90 == 0.4
    assert projection.xa_per_90 == 0.3
    assert projection.xgi_per_90 == 0.7
    assert projection.projected_points > 3


def test_xg_xa_blend_applies_availability() -> None:
    unavailable = replace(player(), chance_of_playing_next_round=0)

    projection = xg_xa_blend(unavailable)

    assert projection.expected_minutes == 0
    assert projection.attacking_points_estimate == 0
