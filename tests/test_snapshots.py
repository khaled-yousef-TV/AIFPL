from datetime import datetime, timezone

import json

import pytest

from aifpl.artifacts import ImmutableArtifactError
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


def test_latest_bootstrap_parses_only_the_newest_payload(tmp_path, monkeypatch) -> None:
    store = SnapshotStore(tmp_path)
    payload = {"elements": [], "teams": [], "events": []}
    for hour in range(10, 20):
        store.save_bootstrap(payload, datetime(2026, 8, 7, hour, 0, tzinfo=timezone.utc))

    loads = json.loads
    parse_calls = 0

    def counting_loads(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return loads(*args, **kwargs)

    monkeypatch.setattr("aifpl.snapshots.json.loads", counting_loads)

    latest_path, summary = store.latest_bootstrap()

    assert parse_calls == 1
    assert latest_path.name == "20260807T190000Z000000.json"
    assert summary.fetched_at == datetime(2026, 8, 7, 19, 0, tzinfo=timezone.utc)


def test_snapshot_store_separates_fixture_and_event_sources(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    fixture_path, fixtures = store.save_fixtures([{"event": 1, "finished": False}], now)
    event_path, event = store.save_event_live(1, {"elements": [{"stats": {"total_points": 5}}]}, now)

    assert fixture_path.parent == store.fixtures_dir
    assert event_path.parent == store.events_dir / "1"
    assert fixtures.fixtures == 1
    assert event.total_points == 5


def test_snapshot_store_never_overwrites_a_timestamp_collision(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    fetched_at = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    payload = {"elements": [], "teams": [], "events": []}
    path, _ = store.save_bootstrap(payload, fetched_at)
    original = path.read_bytes()

    store.save_bootstrap(payload, fetched_at)
    with pytest.raises(ImmutableArtifactError):
        store.save_bootstrap({"elements": [], "teams": [], "events": [{"id": 1}]}, fetched_at)

    assert path.read_bytes() == original


def test_all_snapshot_sources_require_aware_timestamps(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    naive = datetime(2026, 8, 7, 12, 0)

    with pytest.raises(ValueError, match="timezone-aware"):
        store.save_fixtures([], naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        store.save_event_live(1, {"elements": []}, naive)
