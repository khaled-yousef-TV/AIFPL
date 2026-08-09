from datetime import datetime, timedelta, timezone
from pathlib import Path

from aifpl.current import CurrentPlayerCatalogStore
from aifpl.health import SourceHealthChecker
from aifpl.market_odds import EventMarketStore
from aifpl.odds import OddsSnapshotStore
from aifpl.player_evidence import PlayerEvidenceStore
from aifpl.snapshots import SnapshotStore


def bootstrap() -> dict:
    return {"elements": [], "teams": [], "events": []}


def test_source_health_reports_missing_sources_without_crashing(tmp_path) -> None:
    report = SourceHealthChecker(tmp_path).run(datetime(2026, 8, 9, tzinfo=timezone.utc))

    assert report.overall_status == "degraded"
    assert {record.status for record in report.records} >= {"missing", "not_applicable"}
    assert SourceHealthChecker(tmp_path).latest() == report


def test_source_health_distinguishes_fresh_and_stale_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_BOOTSTRAP_MAX_AGE_HOURS", "24")
    monkeypatch.setenv("AIFPL_FIXTURES_MAX_AGE_HOURS", "24")
    monkeypatch.setenv("AIFPL_ODDS_MAX_AGE_HOURS", "6")
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    snapshots = SnapshotStore(tmp_path)
    snapshots.save_bootstrap(bootstrap(), now - timedelta(hours=2))
    snapshots.save_fixtures([], now - timedelta(hours=30))
    odds_payload = [{
        "id": "event", "commence_time": "2026-08-15T14:00:00Z",
        "home_team": "Arsenal", "away_team": "Chelsea",
        "bookmakers": [{"title": "Book", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Arsenal", "price": 2.0}, {"name": "Draw", "price": 4.0},
            {"name": "Chelsea", "price": 4.0},
        ]}]}],
    }]
    OddsSnapshotStore(tmp_path).save_epl_h2h(odds_payload, {}, now - timedelta(hours=1))

    report = SourceHealthChecker(tmp_path).run(now)
    statuses = {record.source: record.status for record in report.records}

    assert statuses["bootstrap"] == "healthy"
    assert statuses["fixtures"] == "stale"
    assert statuses["odds"] == "healthy"


def test_source_health_rejects_fresh_but_empty_odds(tmp_path) -> None:
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    OddsSnapshotStore(tmp_path).save_epl_h2h([], {}, now)

    report = SourceHealthChecker(tmp_path).run(now)

    assert next(record for record in report.records if record.source == "odds").status == "invalid"


class FakeOddsClient:
    def fetch_event_markets(self, event_id: str, markets: list[str]) -> list[dict]:
        return [{
            "id": event_id, "commence_time": "2026-08-15T12:00:00Z",
            "home_team": "A", "away_team": "B",
            "bookmakers": [{"title": "Book", "markets": [{"key": "team_totals", "outcomes": [
                {"name": "Over", "description": "A", "point": 0.5, "price": 1.8},
                {"name": "Under", "description": "A", "point": 0.5, "price": 2.1},
            ]}]}],
        }]


def bootstrap_with_player() -> dict:
    return {"teams": [{"id": 1, "name": "Club"}], "events": [], "elements": [{
        "id": 1, "web_name": "Starter", "first_name": "First", "second_name": "Starter",
        "element_type": 1, "team": 1, "now_cost": 50, "status": "d",
        "chance_of_playing_next_round": 75, "form": "0", "points_per_game": "4", "total_points": 100,
        "minutes": 900, "starts": 10, "expected_goals": "0", "expected_assists": "0",
        "expected_goal_involvements": "0", "expected_goals_conceded": "10",
        "news": "Minor doubt", "news_added": "2026-08-01T10:00:00Z",
        "selected_by_percent": "10", "ep_next": "3",
    }]}


def test_source_health_checks_event_markets_and_player_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_FETCH_EVENT_MARKETS", "true")
    EventMarketStore(tmp_path).fetch(["event-1"], FakeOddsClient())
    SnapshotStore(tmp_path).save_bootstrap(bootstrap_with_player())
    players = CurrentPlayerCatalogStore(tmp_path).normalize_latest()
    PlayerEvidenceStore(tmp_path).build(Path(players.output_path))

    report = SourceHealthChecker(tmp_path).run(datetime.now(timezone.utc))
    statuses = {record.source: record.status for record in report.records}

    assert statuses["event_markets"] == "healthy"
    assert statuses["player_evidence"] == "healthy"


def test_source_health_marks_stale_event_markets_and_player_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_FETCH_EVENT_MARKETS", "true")
    EventMarketStore(tmp_path).fetch(["event-1"], FakeOddsClient())
    SnapshotStore(tmp_path).save_bootstrap(bootstrap_with_player())
    players = CurrentPlayerCatalogStore(tmp_path).normalize_latest()
    PlayerEvidenceStore(tmp_path).build(Path(players.output_path))

    report = SourceHealthChecker(tmp_path).run(datetime.now(timezone.utc) + timedelta(hours=30))
    statuses = {record.source: record.status for record in report.records}

    assert statuses["event_markets"] == "stale"
    assert statuses["player_evidence"] == "stale"


def test_source_health_skips_event_markets_when_disabled(tmp_path) -> None:
    report = SourceHealthChecker(tmp_path).run(datetime.now(timezone.utc))

    statuses = {record.source: record.status for record in report.records}

    assert statuses["event_markets"] == "not_applicable"
