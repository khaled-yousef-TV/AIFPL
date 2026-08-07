from aifpl.fixtures import CurrentFixture
from aifpl.odds import NormalizedMatchOdds
from aifpl.odds_matching import canonical_club_name, match_fixture_odds


def test_club_aliases_handle_fpl_and_bookmaker_names() -> None:
    assert canonical_club_name("Manchester City") == canonical_club_name("Man City")
    assert canonical_club_name("Tottenham Hotspur") == canonical_club_name("Spurs")


def test_fixture_matching_averages_bookmakers_after_normalization() -> None:
    fixture = CurrentFixture(1, 1, "2026-08-15T14:00:00Z", 1, 2, 3, 3, False)
    odds = [
        NormalizedMatchOdds("event", "2026-08-15T14:02:00Z", "Manchester City", "Chelsea", "A", None, 0.6, 0.2, 0.2),
        NormalizedMatchOdds("event", "2026-08-15T14:02:00Z", "Manchester City", "Chelsea", "B", None, 0.7, 0.1, 0.2),
    ]

    matches = match_fixture_odds([fixture], {1: "Man City", 2: "Chelsea"}, odds)

    assert len(matches) == 1
    assert matches[0].bookmakers == 2
    assert matches[0].home_win_probability == 0.65
    assert matches[0].kickoff_delta_seconds == 120


def test_fixture_matching_does_not_guess_when_kickoff_time_is_not_close() -> None:
    fixture = CurrentFixture(1, 1, "2026-08-15T14:00:00Z", 1, 2, 3, 3, False)
    odds = [NormalizedMatchOdds("event", "2026-08-15T14:10:01Z", "Arsenal", "Chelsea", "A", None, 0.6, 0.2, 0.2)]

    assert match_fixture_odds([fixture], {1: "Arsenal", 2: "Chelsea"}, odds) == []
