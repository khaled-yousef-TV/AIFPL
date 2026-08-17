import json
import os
import threading
from datetime import datetime, timezone

import pytest

from aifpl.config import SchedulerSettings
from aifpl.refresh import RefreshJobError, RefreshJobResult
from aifpl.scheduler import CLAIM_LEASE_SECONDS, DeadlineScheduler, SchedulerTickError
from aifpl.snapshots import SnapshotStore


def bootstrap(deadline: str) -> dict:
    return {"elements": [], "teams": [], "events": [{"id": 1, "deadline_time": deadline, "is_next": True}]}


class FakeRefreshJob:
    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.calls: list[tuple[int, int, int]] = []

    def run(self, start: int, end: int, budget: int) -> RefreshJobResult:
        self.calls.append((start, end, budget))
        now = datetime(2026, 8, 15, 10, tzinfo=timezone.utc)
        return RefreshJobResult(
            status="succeeded", started_at=now, completed_at=now, start_gameweek=start,
            end_gameweek=end, budget=budget, completed_steps=[], artifacts={},
            health_status="healthy", output_path=str(self.tmp_path / "refresh.json"),
        )


class FailingRefreshJob(FakeRefreshJob):
    def run(self, start: int, end: int, budget: int) -> RefreshJobResult:
        result = super().run(start, end, budget)
        failed = result.model_copy(update={"status": "failed", "error": "unavailable"})
        raise RefreshJobError(failed)


def scheduler(tmp_path, refresh_job: FakeRefreshJob) -> DeadlineScheduler:
    return DeadlineScheduler(tmp_path, refresh_job=refresh_job, settings=SchedulerSettings(90, 6, 300, 1000))


def test_scheduler_detects_deadline_without_running_early(tmp_path) -> None:
    SnapshotStore(tmp_path).save_bootstrap(bootstrap("2026-08-15T12:00:00Z"), datetime(2026, 8, 1, tzinfo=timezone.utc))
    refresh = FakeRefreshJob(tmp_path)
    result = scheduler(tmp_path, refresh).tick(datetime(2026, 8, 15, 10, tzinfo=timezone.utc))

    assert result.status == "not_due"
    assert result.refresh_at == datetime(2026, 8, 15, 10, 30, tzinfo=timezone.utc)
    assert refresh.calls == []


def test_scheduler_runs_due_event_once(tmp_path) -> None:
    SnapshotStore(tmp_path).save_bootstrap(bootstrap("2026-08-15T12:00:00Z"), datetime(2026, 8, 1, tzinfo=timezone.utc))
    refresh = FakeRefreshJob(tmp_path)
    subject = scheduler(tmp_path, refresh)

    first = subject.tick(datetime(2026, 8, 15, 11, tzinfo=timezone.utc))
    second = subject.tick(datetime(2026, 8, 15, 11, 5, tzinfo=timezone.utc))

    assert first.status == "succeeded"
    assert second.status == "already_completed"
    assert refresh.calls == [(1, 6, 1000)]


def test_scheduler_marks_missed_deadline_without_late_refresh(tmp_path) -> None:
    SnapshotStore(tmp_path).save_bootstrap(bootstrap("2026-08-15T12:00:00Z"), datetime(2026, 8, 1, tzinfo=timezone.utc))
    refresh = FakeRefreshJob(tmp_path)

    result = scheduler(tmp_path, refresh).tick(datetime(2026, 8, 15, 13, tzinfo=timezone.utc))

    assert result.status == "missed"
    assert result.missed is True
    assert refresh.calls == []


def test_scheduler_completion_is_isolated_by_season(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    store.save_bootstrap(bootstrap("2026-08-15T12:00:00Z"), datetime(2026, 8, 1, tzinfo=timezone.utc))
    refresh = FakeRefreshJob(tmp_path)
    subject = scheduler(tmp_path, refresh)
    first = subject.tick(datetime(2026, 8, 15, 11, tzinfo=timezone.utc))

    store.save_bootstrap(bootstrap("2027-08-14T12:00:00Z"), datetime(2027, 8, 1, tzinfo=timezone.utc))
    second = subject.tick(datetime(2027, 8, 14, 11, tzinfo=timezone.utc))

    assert first.season_id == "2026-27"
    assert second.season_id == "2027-28"
    assert second.status == "succeeded"
    assert refresh.calls == [(1, 6, 1000), (1, 6, 1000)]
    assert (tmp_path / "scheduler/completed/2026-27/gw1.json").exists()
    assert (tmp_path / "scheduler/completed/2027-28/gw1.json").exists()


def test_concurrent_ticks_cannot_both_refresh(tmp_path) -> None:
    SnapshotStore(tmp_path).save_bootstrap(bootstrap("2026-08-15T12:00:00Z"), datetime(2026, 8, 1, tzinfo=timezone.utc))
    started = threading.Event()
    release = threading.Event()

    class BlockingRefreshJob(FakeRefreshJob):
        def run(self, start: int, end: int, budget: int) -> RefreshJobResult:
            started.set()
            assert release.wait(2)
            return super().run(start, end, budget)

    refresh = BlockingRefreshJob(tmp_path)
    subject = scheduler(tmp_path, refresh)
    first: list = []
    thread = threading.Thread(
        target=lambda: first.append(subject.tick(datetime(2026, 8, 15, 11, tzinfo=timezone.utc))),
    )
    thread.start()
    assert started.wait(2)

    concurrent = subject.tick(datetime(2026, 8, 15, 11, tzinfo=timezone.utc))
    release.set()
    thread.join(2)

    assert not thread.is_alive()
    assert concurrent.status == "in_progress"
    assert first[0].status == "succeeded"
    assert refresh.calls == [(1, 6, 1000)]


def test_preexisting_claim_prevents_duplicate_refresh(tmp_path) -> None:
    SnapshotStore(tmp_path).save_bootstrap(bootstrap("2026-08-15T12:00:00Z"), datetime(2026, 8, 1, tzinfo=timezone.utc))
    refresh = FakeRefreshJob(tmp_path)
    claim = tmp_path / "scheduler/locks/2026-27/gw1.json"
    claim.parent.mkdir(parents=True)
    claim.write_text('{"token":"other"}', encoding="utf-8")

    result = scheduler(tmp_path, refresh).tick(datetime(2026, 8, 15, 11, tzinfo=timezone.utc))

    assert result.status == "in_progress"
    assert refresh.calls == []


def test_stale_claim_is_recovered(tmp_path) -> None:
    SnapshotStore(tmp_path).save_bootstrap(bootstrap("2026-08-15T12:00:00Z"), datetime(2026, 8, 1, tzinfo=timezone.utc))
    refresh = FakeRefreshJob(tmp_path)
    claim = tmp_path / "scheduler/locks/2026-27/gw1.json"
    claim.parent.mkdir(parents=True)
    claim.write_text('{"token":"abandoned"}', encoding="utf-8")
    stale = datetime.now(timezone.utc).timestamp() - CLAIM_LEASE_SECONDS - 1
    os.utime(claim, (stale, stale))

    result = scheduler(tmp_path, refresh).tick(datetime(2026, 8, 15, 11, tzinfo=timezone.utc))

    assert result.status == "succeeded"
    assert refresh.calls == [(1, 6, 1000)]
    assert not claim.exists()


def test_failed_refresh_releases_claim_for_retry(tmp_path) -> None:
    SnapshotStore(tmp_path).save_bootstrap(bootstrap("2026-08-15T12:00:00Z"), datetime(2026, 8, 1, tzinfo=timezone.utc))
    subject = scheduler(tmp_path, FailingRefreshJob(tmp_path))

    with pytest.raises(SchedulerTickError):
        subject.tick(datetime(2026, 8, 15, 11, tzinfo=timezone.utc))

    refresh = FakeRefreshJob(tmp_path)
    retry = scheduler(tmp_path, refresh).tick(datetime(2026, 8, 15, 11, 1, tzinfo=timezone.utc))
    assert retry.status == "succeeded"
    assert refresh.calls == [(1, 6, 1000)]


def test_deadline_discovery_failure_is_audited(tmp_path) -> None:
    with pytest.raises(SchedulerTickError) as caught:
        scheduler(tmp_path, FakeRefreshJob(tmp_path)).tick(datetime(2026, 8, 15, 11, tzinfo=timezone.utc))

    result = caught.value.result
    assert result.status == "discovery_failed"
    assert result.event is None
    assert json.loads((tmp_path / result.output_path).read_text(encoding="utf-8"))["status"] == "discovery_failed"


def test_scheduler_stops_without_waiting_for_the_poll_interval(tmp_path) -> None:
    subject = scheduler(tmp_path, FakeRefreshJob(tmp_path))
    stop_event = threading.Event()
    calls: list[None] = []

    def stop_after_first_tick() -> None:
        calls.append(None)
        stop_event.set()
        raise RuntimeError("stop")

    subject.tick = stop_after_first_tick  # type: ignore[method-assign]
    thread = threading.Thread(target=subject.run_forever, args=(stop_event,))
    thread.start()
    thread.join(1)

    assert calls == [None]
    assert not thread.is_alive()
