from aifpl.market_odds import EVENT_MARKETS, EventMarketStore, normalize_event_markets


def test_default_event_markets_exclude_unused_anytime_scorers() -> None:
    assert EVENT_MARKETS == ("team_totals", "player_assists")


def test_complete_over_under_market_is_margin_adjusted() -> None:
    payload = [{"id": "e", "commence_time": "2026-08-15T12:00:00Z", "home_team": "A", "away_team": "B", "bookmakers": [{"title": "Book", "markets": [{"key": "team_totals", "outcomes": [{"name": "Over", "description": "A", "point": 0.5, "price": 1.8}, {"name": "Under", "description": "A", "point": 0.5, "price": 2.1}]}]}]}]

    quotes = normalize_event_markets(payload)

    assert round(sum(quote.margin_adjusted_probability for quote in quotes), 6) == 1


def test_one_sided_scorer_market_remains_unadjusted() -> None:
    payload = [{"id": "e", "commence_time": "2026-08-15T12:00:00Z", "home_team": "A", "away_team": "B", "bookmakers": [{"title": "Book", "markets": [{"key": "player_goal_scorer_anytime", "outcomes": [{"name": "Player", "price": 2.0}]}]}]}]

    assert normalize_event_markets(payload)[0].margin_adjusted_probability is None


def test_event_market_fetch_caps_the_number_of_events(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    class FakeOddsClient:
        def fetch_event_markets(self, event_id: str, markets: tuple[str, ...]):
            calls.append(event_id)
            return {
                "id": event_id, "commence_time": "2026-08-15T12:00:00Z", "home_team": "A", "away_team": "B",
                "bookmakers": [{"title": "Book", "markets": [{"key": "team_totals", "outcomes": [
                    {"name": "Over", "description": "A", "point": 0.5, "price": 1.8},
                    {"name": "Under", "description": "A", "point": 0.5, "price": 2.1},
                ]}]}],
            }, {}

    monkeypatch.setenv("AIFPL_EVENT_MARKET_MAX_EVENTS", "2")
    catalog = EventMarketStore(tmp_path).fetch(["first", "second", "first", "third"], FakeOddsClient())

    assert calls == ["first", "second"]
    assert catalog.events_requested == 2
