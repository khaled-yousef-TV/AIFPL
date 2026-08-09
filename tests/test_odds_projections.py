from dataclasses import replace

from aifpl.current import CurrentPlayer
from aifpl.fixtures import CurrentFixture
from aifpl.odds_matching import FixtureOddsConsensus
from aifpl.odds_projections import build_odds_adjusted_projections
from aifpl.market_signals import TeamCleanSheetSignal


def player() -> CurrentPlayer:
    return CurrentPlayer(1, "Test", "MID", 1, "Arsenal", 100, "a", None, 0, 5, 100, 1900, 25, 5, 4, 9, 20)


def test_odds_projection_applies_win_adjustment_only_when_fixture_is_matched() -> None:
    fixture = CurrentFixture(1, 1, "2026-08-15T14:00:00Z", 1, 2, 3, 3, False)
    consensus = FixtureOddsConsensus(1, 1, "event", "Arsenal", "Chelsea", "2026-08-15T14:00:00Z", 0, 10, 0.75, 0.15, 0.10)

    projections = build_odds_adjusted_projections([player()], [fixture], [consensus], 1, 2)

    assert projections[0].fixture_count == 1
    assert projections[0].odds_backed_fixture_count == 1
    assert projections[0].projected_points > 0
    assert projections[1].fixture_count == 0
    assert projections[1].projected_points == 0


def test_odds_projection_keeps_unmatched_fixture_without_odds_adjustment() -> None:
    fixture = CurrentFixture(2, 1, "2026-08-15T14:00:00Z", 1, 2, 3, 3, False)

    projection = build_odds_adjusted_projections([player()], [fixture], [], 1, 1)[0]

    assert projection.odds_backed_fixture_count == 0


def test_odds_projection_excludes_finished_fixture() -> None:
    fixture = CurrentFixture(2, 1, "2026-08-15T14:00:00Z", 1, 2, 3, 3, True)

    projection = build_odds_adjusted_projections([player()], [fixture], [], 1, 1)[0]

    assert projection.fixture_count == 0
    assert projection.projected_points == 0


def test_clean_sheet_signal_is_scaled_by_expected_participation() -> None:
    unavailable = replace(player(), position="DEF", chance_of_playing_next_round=0)
    fixture = CurrentFixture(2, 1, "2026-08-15T14:00:00Z", 1, 2, 3, 3, False)
    signal = TeamCleanSheetSignal(2, "Arsenal", 0.8, 2)

    projection = build_odds_adjusted_projections([unavailable], [fixture], [], 1, 1, clean_sheet_signals=[signal])[0]

    assert projection.projected_points == 0
