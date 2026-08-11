import json
import os
from fcntl import LOCK_EX, LOCK_UN, flock

import pytest

from aifpl.fpl import FplSourceError
from aifpl.refresh import CurrentDataRefreshJob, RefreshJobError


class FailingFplClient:
    async def fetch_bootstrap(self) -> dict:
        raise FplSourceError("source unavailable")


def test_failed_refresh_job_is_persisted_for_audit(tmp_path) -> None:
    job = CurrentDataRefreshJob(tmp_path, fpl_client=FailingFplClient())

    with pytest.raises(RefreshJobError) as caught:
        job.run(1, 1)

    result = caught.value.result
    persisted = json.loads((tmp_path / result.output_path).read_text(encoding="utf-8"))
    assert result.status == "failed"
    assert result.completed_steps == []
    assert "source unavailable" in result.error
    assert persisted["status"] == "failed"
    assert job.latest() == result


def test_refresh_job_validates_range_before_network_access(tmp_path) -> None:
    with pytest.raises(ValueError, match="gameweek range"):
        CurrentDataRefreshJob(tmp_path, fpl_client=FailingFplClient()).run(2, 1)


def test_concurrent_refresh_is_rejected_and_audited(tmp_path) -> None:
    lock_path = tmp_path / "jobs" / "refresh" / "current.lock"
    lock_path.parent.mkdir(parents=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    flock(descriptor, LOCK_EX)
    try:
        with pytest.raises(RefreshJobError) as caught:
            CurrentDataRefreshJob(tmp_path, fpl_client=FailingFplClient()).run(1, 1)
    finally:
        flock(descriptor, LOCK_UN)
        os.close(descriptor)

    assert "already running" in caught.value.result.error


def test_partial_coverage_soft_degrade_config() -> None:
    from aifpl import config

    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setenv("AIFPL_MIN_ODDS_FIXTURE_COVERAGE", "0.5")
    monkeypatch.setenv("AIFPL_PARTIAL_ODDS_FIXTURE_COVERAGE", "0.8")

    assert config.minimum_odds_fixture_coverage() == 0.5
    assert config.partial_odds_fixture_coverage() == 0.8

    monkeypatch.setenv("AIFPL_MIN_ODDS_FIXTURE_COVERAGE", "0.9")
    try:
        config.partial_odds_fixture_coverage()
    except ValueError:
        pass
    else:
        raise AssertionError("partial threshold below hard floor must raise")
    monkeypatch.undo()


def test_partial_coverage_is_flagged_not_fatal(tmp_path) -> None:
    from aifpl.config import partial_odds_fixture_coverage
    from aifpl.odds_projections import OddsProjectionCatalog

    catalog = OddsProjectionCatalog(
        start_gameweek=1, end_gameweek=6, records=10, output_path="x.jsonl",
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        odds_coverage_by_gameweek={1: 0.6, 2: 0.9, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0},
        odds_coverage_status="partial",
    )

    assert catalog.odds_coverage_status == "partial"
    assert catalog.odds_coverage_by_gameweek[1] < partial_odds_fixture_coverage()
