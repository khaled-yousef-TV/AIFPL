from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from aifpl.artifacts import json_bytes, write_immutable
from aifpl.config import freshness_hours
from aifpl.odds import OddsSnapshotStore
from aifpl.snapshots import SnapshotNotFoundError, SnapshotStore


HealthStatus = Literal["healthy", "stale", "missing", "invalid", "not_applicable"]


class SourceHealthRecord(BaseModel):
    source: str
    status: HealthStatus
    checked_at: datetime
    fetched_at: datetime | None = None
    age_seconds: float | None = None
    max_age_seconds: float | None = None
    path: str | None = None
    detail: str


class SourceHealthReport(BaseModel):
    overall_status: Literal["healthy", "degraded"]
    checked_at: datetime
    records: list[SourceHealthRecord]
    output_path: str


class SourceHealthChecker:
    def __init__(self, root: Path) -> None:
        self.root = root

    def run(self, checked_at: datetime | None = None) -> SourceHealthReport:
        now = checked_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("checked_at must be timezone-aware")
        now = now.astimezone(timezone.utc)
        snapshots = SnapshotStore(self.root)
        records = [
            self._check_snapshot("bootstrap", now, snapshots.latest_bootstrap),
            self._check_snapshot("fixtures", now, snapshots.latest_fixtures),
        ]
        current_event = None
        try:
            _, bootstrap = snapshots.latest_bootstrap()
            current_event = bootstrap.current_event
        except (SnapshotNotFoundError, ValueError, KeyError):
            pass
        if current_event is None:
            records.append(SourceHealthRecord(
                source="event_live", status="not_applicable", checked_at=now,
                detail="Bootstrap does not identify a current gameweek",
            ))
        else:
            records.append(self._check_snapshot(
                "event_live", now, lambda: snapshots.latest_event_live(current_event),
            ))
        records.append(self._check_odds(now))
        overall = "healthy" if all(record.status in ("healthy", "not_applicable") for record in records) else "degraded"
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / "health" / "sources" / f"{stamp}.json"
        report = SourceHealthReport(overall_status=overall, checked_at=now, records=records, output_path=str(path))
        write_immutable(path, json_bytes(report.model_dump(mode="json"), pretty=True))
        return report

    def latest(self) -> SourceHealthReport:
        directory = self.root / "health" / "sources"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No source health report exists; run check-source-health first")
        return SourceHealthReport.model_validate_json(files[-1].read_text(encoding="utf-8"))

    def _check_snapshot(self, source: str, now: datetime, loader: object) -> SourceHealthRecord:
        try:
            path, summary = loader()
            return self._freshness_record(source, path, summary.fetched_at, now)
        except SnapshotNotFoundError as exc:
            return SourceHealthRecord(source=source, status="missing", checked_at=now, detail=str(exc))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return SourceHealthRecord(source=source, status="invalid", checked_at=now, detail=str(exc))

    def _check_odds(self, now: datetime) -> SourceHealthRecord:
        try:
            store = OddsSnapshotStore(self.root)
            path = store.latest_path()
            rows = store.load(path)
            if not rows:
                raise ValueError("Normalized odds catalog contains no usable bookmaker markets")
            raw_path = self.root / "raw" / "odds" / "the_odds_api" / "epl" / f"{path.stem}.json"
            document = json.loads(raw_path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(document["metadata"]["fetched_at"])
            return self._freshness_record("odds", path, fetched_at, now)
        except FileNotFoundError as exc:
            return SourceHealthRecord(source="odds", status="missing", checked_at=now, detail=str(exc))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return SourceHealthRecord(source="odds", status="invalid", checked_at=now, detail=str(exc))

    @staticmethod
    def _freshness_record(source: str, path: Path, fetched_at: datetime, now: datetime) -> SourceHealthRecord:
        if fetched_at.tzinfo is None:
            raise ValueError(f"{source} fetched_at must be timezone-aware")
        age = max(0.0, (now - fetched_at.astimezone(timezone.utc)).total_seconds())
        maximum = freshness_hours(source) * 3600
        status: HealthStatus = "healthy" if age <= maximum else "stale"
        detail = "Source is within its freshness limit" if status == "healthy" else "Source exceeds its freshness limit"
        return SourceHealthRecord(
            source=source, status=status, checked_at=now, fetched_at=fetched_at,
            age_seconds=round(age, 3), max_age_seconds=maximum, path=str(path), detail=detail,
        )
