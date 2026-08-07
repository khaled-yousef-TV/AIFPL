from datetime import datetime, timezone

from aifpl.snapshots import SnapshotStore


def test_snapshot_store_saves_and_loads_latest(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    payload = {"elements": [], "teams": [], "events": []}
    expected_time = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    path, saved = store.save_bootstrap(payload, expected_time)
    latest_path, loaded = store.latest_bootstrap()

    assert path == latest_path
    assert saved == loaded
    assert loaded.fetched_at == expected_time


def test_snapshot_store_selects_only_data_available_before_cutoff(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    payload = {"elements": [], "teams": [], "events": []}
    first_time = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 8, 7, 14, 0, tzinfo=timezone.utc)
    first_path, _ = store.save_bootstrap(payload, first_time)
    store.save_bootstrap(payload, second_time)

    selected_path, _ = store.bootstrap_before(datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc))

    assert selected_path == first_path


def test_snapshot_store_separates_fixture_and_event_sources(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    fixture_path, fixtures = store.save_fixtures([{"event": 1, "finished": False}], now)
    event_path, event = store.save_event_live(1, {"elements": [{"stats": {"total_points": 5}}]}, now)

    assert fixture_path.parent == store.fixtures_dir
    assert event_path.parent == store.events_dir / "1"
    assert fixtures.fixtures == 1
    assert event.total_points == 5
