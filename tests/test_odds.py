from datetime import datetime, timezone

from aifpl.odds import OddsSnapshotStore, normalize_epl_h2h


PAYLOAD = [{"id": "event-1", "commence_time": "2026-08-15T14:00:00Z", "home_team": "Arsenal", "away_team": "Chelsea", "bookmakers": [{"title": "Test Book", "last_update": "2026-08-01T10:00:00Z", "markets": [{"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 2.0}, {"name": "Draw", "price": 4.0}, {"name": "Chelsea", "price": 4.0}]}]}]}]


def test_normalize_epl_h2h_removes_bookmaker_margin() -> None:
    odds = normalize_epl_h2h(PAYLOAD)

    assert len(odds) == 1
    assert odds[0].home_win_probability == 0.5
    assert odds[0].draw_probability == 0.25
    assert odds[0].away_win_probability == 0.25


def test_odds_snapshot_store_keeps_raw_and_normalized_data(tmp_path) -> None:
    summary = OddsSnapshotStore(tmp_path).save_epl_h2h(PAYLOAD, {"x-requests-remaining": "499", "x-requests-used": "1", "x-requests-last": "1"}, datetime(2026, 8, 7, tzinfo=timezone.utc))

    latest = OddsSnapshotStore(tmp_path).latest_epl_h2h()

    assert summary.events == 1
    assert summary.bookmaker_markets == 1
    assert summary.requests_remaining == "499"
    assert latest[0].home_team == "Arsenal"
