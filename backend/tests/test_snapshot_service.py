"""Tests for the daily-snapshot squad validation logic."""

from types import SimpleNamespace

from services.snapshot_service import find_invalid_squad_players


def player(pid, status="a", chance=100, news="", web="P", second="Last"):
    return SimpleNamespace(
        id=pid, status=status, chance_of_playing_next_round=chance,
        news=news, web_name=web, second_name=second,
    )


def squad(*ids):
    half = len(ids) // 2 or 1
    return {
        "starting_xi": [{"id": i} for i in ids[:half]],
        "bench": [{"id": i} for i in ids[half:]],
    }


def test_all_available_squad_is_valid():
    players = [player(i) for i in range(1, 6)]
    assert find_invalid_squad_players(squad(1, 2, 3, 4, 5), players) == []


def test_flags_injured_and_suspended():
    players = [player(1, status="i"), player(2, status="s"), player(3)]
    invalid = find_invalid_squad_players(squad(1, 2, 3), players)
    assert len(invalid) == 2
    assert any("status: i" in s for s in invalid)
    assert any("status: s" in s for s in invalid)


def test_flags_low_chance_of_playing():
    players = [player(1, chance=25), player(2, chance=75)]
    invalid = find_invalid_squad_players(squad(1, 2), players)
    assert len(invalid) == 1
    assert "chance: 25%" in invalid[0]


def test_flags_negative_news_even_when_available():
    players = [player(1, status="a", news="Knock - 75% chance, minor injury")]
    invalid = find_invalid_squad_players(squad(1), players)
    assert len(invalid) == 1
    assert "news:" in invalid[0]


def test_unknown_player_ids_are_skipped():
    # squad references a player not in the bootstrap list -> skipped, not crash
    assert find_invalid_squad_players(squad(99), [player(1)]) == []


def test_none_chance_is_treated_as_available():
    assert find_invalid_squad_players(squad(1), [player(1, chance=None)]) == []
