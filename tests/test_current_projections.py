from aifpl.current import CurrentPlayer
from aifpl.current_projections import CurrentProjectionStore, fpl_source_baseline


def player(form: float = 0.0, chance: int | None = None, status: str = "a") -> CurrentPlayer:
    return CurrentPlayer(
        id=1, name="Test", position="MID", club_id=1, club="Arsenal", cost=100, status=status,
        chance_of_playing_next_round=chance, form=form, points_per_game=5.0, total_points=100,
        minutes=2000, starts=25, expected_goals=5, expected_assists=4, expected_goal_involvements=9, expected_goals_conceded=20,
    )


def test_source_baseline_uses_points_per_game_when_form_is_zero() -> None:
    projection = fpl_source_baseline(player())

    assert projection.projected_points == 5.0
    assert projection.availability_multiplier == 1.0


def test_source_baseline_blends_positive_form_and_availability() -> None:
    projection = fpl_source_baseline(player(form=7.0, chance=50))

    assert projection.projected_points == 2.8
    assert projection.availability_multiplier == 0.5


def test_source_baseline_zeros_players_out_of_action_without_chance_value() -> None:
    projection = fpl_source_baseline(player(status="i", chance=None))

    assert projection.availability_multiplier == 0.0
    assert projection.projected_points == 0.0


def test_source_baseline_treats_status_doubt_without_chance_value_as_half() -> None:
    projection = fpl_source_baseline(player(status="d", chance=None))

    assert projection.availability_multiplier == 0.5
