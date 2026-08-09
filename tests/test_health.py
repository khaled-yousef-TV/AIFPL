from datetime import datetime, timedelta, timezone

from aifpl.health import SourceHealthChecker
from aifpl.odds import OddsSnapshotStore
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
