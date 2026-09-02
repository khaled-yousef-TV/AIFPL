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

    assert projection.expected_minutes == 90
    assert projection.xg_per_90 == 0.4
    assert projection.xa_per_90 == 0.3
    assert projection.xgi_per_90 == 0.7
    assert projection.projected_points > 3


def test_xg_xa_blend_scales_starts_against_elapsed_opportunities() -> None:
    projection = xg_xa_blend(player(), gameweeks_elapsed=20)

    assert projection.expected_minutes == 45
    assert projection.appearance_probability == 0.5
    assert projection.start_probability == 0.5
    assert projection.conditional_minutes == 90


def test_xg_xa_blend_does_not_double_discount_substitute_minutes() -> None:
    substitute_heavy = replace(player(), minutes=900, starts=5)

    projection = xg_xa_blend(substitute_heavy, gameweeks_elapsed=20)

    assert projection.expected_minutes == 45
    assert projection.appearance_probability == 0.5
    assert projection.start_probability == 0.25
    assert projection.conditional_minutes == 90


def test_xg_xa_blend_applies_availability() -> None:
    unavailable = replace(player(), chance_of_playing_next_round=0)

    projection = xg_xa_blend(unavailable)

    assert projection.expected_minutes == 0
    assert projection.attacking_points_estimate == 0


def test_xg_xa_blend_limits_next_round_availability_to_first_horizon_week() -> None:
    unavailable = replace(player(), chance_of_playing_next_round=0)

    projection = xg_xa_blend(unavailable, apply_next_round_availability=False)

    assert projection.expected_minutes == 90


def test_xg_xa_blend_does_not_treat_one_backup_start_as_full_time() -> None:
    backup = replace(
        player(), position="GK", minutes=90, starts=1, points_per_game=7,
        expected_goals=0, expected_assists=0, expected_goal_involvements=0,
    )

    projection = xg_xa_blend(backup, gameweeks_elapsed=38)

    assert projection.expected_minutes == 2.3684
    assert projection.projected_points < 0.2


def test_explicit_predicted_start_probability_overrides_historical_rate() -> None:
    projection = xg_xa_blend(player(), gameweeks_elapsed=20, start_probability_override=0.9)

    assert projection.expected_minutes == 81


def test_minutes_multiplier_override_scales_expected_minutes() -> None:
    projection = xg_xa_blend(player(), minutes_multiplier_override=0.7)

    assert projection.expected_minutes == 63


def test_minutes_multiplier_override_rejects_invalid_range() -> None:
    import pytest

    with pytest.raises(ValueError, match="minutes_multiplier_override"):
        xg_xa_blend(player(), minutes_multiplier_override=2.0)
