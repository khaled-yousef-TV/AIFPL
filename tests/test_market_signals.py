from aifpl.current import CurrentPlayer
from aifpl.market_odds import NormalizedMarketQuote
from aifpl.market_signals import build_market_signals
from aifpl.odds_matching import FixtureOddsConsensus


def test_market_signals_require_complete_margin_adjusted_quotes() -> None:
    consensus = [FixtureOddsConsensus(1, 1, "e", "Arsenal", "Chelsea", "2026-08-15T12:00:00Z", 0, 1, .5, .25, .25)]
    player = CurrentPlayer(1, "Saka", "MID", 1, "Arsenal", 100, "a", None, 0, 5, 0, 900, 10, 1, 1, 2, 10, "Bukayo", "Saka")
    quotes = [
        NormalizedMarketQuote("e", "2026-08-15T12:00:00Z", "Arsenal", "Chelsea", "Book", None, "team_totals", "Chelsea", "Under", .5, 2, .5, .55),
        NormalizedMarketQuote("e", "2026-08-15T12:00:00Z", "Arsenal", "Chelsea", "Book", None, "player_assists", "Bukayo Saka", "Over", .5, 2, .5, .52),
    ]

    clean, props = build_market_signals(quotes, consensus, [player])

    assert clean[0].team_name == "Arsenal"
    assert props[0].player_id == 1
