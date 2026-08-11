from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from aifpl.artifacts import json_bytes, write_immutable
from aifpl.config import SchedulerSettings, scheduler_settings
from aifpl.refresh import CurrentDataRefreshJob, RefreshJobError
from aifpl.security import redact_secrets
from aifpl.snapshots import SnapshotStore


CLAIM_LEASE_SECONDS = 6 * 60 * 60


class DeadlineStatus(BaseModel):
    checked_at: datetime
    event: int
    deadline: datetime
    refresh_at: datetime
    due: bool
    completed: bool
    missed: bool
    season_id: str
    source_snapshot: str


class SchedulerTickResult(BaseModel):
    status: Literal[
        "not_due", "already_completed", "in_progress", "succeeded", "failed",
        "discovery_failed", "missed",
    ]
    checked_at: datetime
    event: int | None
    deadline: datetime | None
    refresh_at: datetime | None
    season_id: str | None
    missed: bool | None
    forced: bool
    refresh_job_path: str | None = None
    hermes_decision_path: str | None = None
    hermes_error: str | None = None
    telegram_notified: bool | None = None
    telegram_error: str | None = None
    error: str | None = None
    output_path: str


class SchedulerTickError(RuntimeError):
    def __init__(self, result: SchedulerTickResult) -> None:
        super().__init__(result.error or "Scheduled refresh failed")
        self.result = result


class DeadlineScheduler:
    def __init__(
        self, root: Path, refresh_job: CurrentDataRefreshJob | None = None,
        settings: SchedulerSettings | None = None,
    ) -> None:
        self.root = root
        self.refresh_job = refresh_job or CurrentDataRefreshJob(root)
        self.settings = settings or scheduler_settings()

    def status(self, checked_at: datetime | None = None) -> DeadlineStatus:
        now = checked_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("checked_at must be timezone-aware")
        now = now.astimezone(timezone.utc)
        source_path, _ = SnapshotStore(self.root).latest_bootstrap()
        document = json.loads(source_path.read_text(encoding="utf-8"))
        deadlines: list[tuple[datetime, int]] = []
        for event in document.get("payload", {}).get("events", []):
            if not isinstance(event, dict) or not isinstance(event.get("id"), int) or not isinstance(event.get("deadline_time"), str):
                continue
            deadline = datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                raise ValueError(f"Deadline for event {event['id']} must be timezone-aware")
            deadlines.append((deadline.astimezone(timezone.utc), event["id"]))
        if not deadlines:
            raise ValueError("Bootstrap snapshot has no FPL deadlines")
        upcoming = [item for item in deadlines if item[0] > now]
        deadline, event = min(upcoming) if upcoming else max(deadlines)
        refresh_at = deadline - timedelta(minutes=self.settings.lead_minutes)
        season_id = self._season_id(deadline)
        return DeadlineStatus(
            checked_at=now, event=event, deadline=deadline, refresh_at=refresh_at,
            due=now >= refresh_at,
            completed=self._completion_path(season_id, event).exists(),
            missed=now >= deadline,
            season_id=season_id,
            source_snapshot=str(source_path),
        )

    def tick(self, checked_at: datetime | None = None, force: bool = False) -> SchedulerTickResult:
        result = self._tick_inner(checked_at, force)
        if result.event is not None and result.deadline is not None and result.status not in ("missed", "discovery_failed"):
            notified, notify_error = self._maybe_notify_telegram(
                result.event, result.season_id or "", result.deadline, result.checked_at,
            )
            result = result.model_copy(update={"telegram_notified": notified, "telegram_error": notify_error})
        return result

    def _tick_inner(self, checked_at: datetime | None = None, force: bool = False) -> SchedulerTickResult:
        try:
            schedule = self.status(checked_at)
        except Exception as exc:
            now = checked_at if checked_at is not None and checked_at.tzinfo is not None else datetime.now(timezone.utc)
            result = self._persist_discovery_failure(now.astimezone(timezone.utc), force, exc)
            raise SchedulerTickError(result) from exc
        output_path = self._tick_path(schedule.checked_at)
        if schedule.completed:
            if os.environ.get("AIFPL_HERMES_AUTO_RUN", "false").lower() in ("1", "true", "yes") and not self._hermes_completion_path(schedule.season_id, schedule.event).exists():
                try:
                    decision_path = self._run_hermes_for_event(schedule.event, schedule.season_id)
                    result = self._persist_tick(output_path, schedule, "already_completed", force, hermes_decision_path=decision_path)
                    write_immutable(self._hermes_completion_path(schedule.season_id, schedule.event), json_bytes({"decision_path": decision_path}, pretty=True))
                    return result
                except Exception as exc:
                    return self._persist_tick(
                        output_path, schedule, "already_completed", force,
                        hermes_error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                    )
            return self._persist_tick(output_path, schedule, "already_completed", force)
        if schedule.missed:
            result = self._persist_tick(output_path, schedule, "missed", force)
            write_immutable(
                self._completion_path(schedule.season_id, schedule.event),
                json_bytes(result.model_dump(mode="json"), pretty=True),
            )
            return result
        if not schedule.due and not force:
            return self._persist_tick(output_path, schedule, "not_due", force)
        claim = self._claim(schedule)
        if claim is None:
            status = "already_completed" if self._completion_path(schedule.season_id, schedule.event).exists() else "in_progress"
            return self._persist_tick(output_path, schedule, status, force)
        try:
            if self._completion_path(schedule.season_id, schedule.event).exists():
                return self._persist_tick(output_path, schedule, "already_completed", force)
            end_gameweek = min(38, schedule.event + self.settings.horizon_gameweeks - 1)
            refresh = self.refresh_job.run(schedule.event, end_gameweek, self.settings.budget)
            hermes_decision_path = None
            hermes_error = None
            if os.environ.get("AIFPL_HERMES_AUTO_RUN", "false").lower() in ("1", "true", "yes"):
                try:
                    hermes_decision_path = self._run_hermes_for_event(schedule.event, schedule.season_id)
                except Exception as exc:
                    hermes_error = redact_secrets(f"{type(exc).__name__}: {exc}")
            result = self._persist_tick(
                output_path, schedule, "succeeded", force, refresh_job_path=refresh.output_path,
                hermes_decision_path=hermes_decision_path,
                hermes_error=hermes_error,
            )
            write_immutable(
                self._completion_path(schedule.season_id, schedule.event),
                json_bytes(result.model_dump(mode="json"), pretty=True),
            )
            if hermes_decision_path:
                write_immutable(
                    self._hermes_completion_path(schedule.season_id, schedule.event),
                    json_bytes({"decision_path": hermes_decision_path}, pretty=True),
                )
            return result
        except Exception as exc:
            refresh_path = exc.result.output_path if isinstance(exc, RefreshJobError) else None
            result = self._persist_tick(
                output_path, schedule, "failed", force,
                refresh_job_path=refresh_path, error=redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            raise SchedulerTickError(result) from exc
        finally:
            self._release_claim(claim)

    def run_forever(self) -> None:
        while True:
            try:
                result = self.tick()
                print(
                    f"[{result.checked_at.isoformat()}] tick={result.status} "
                    f"event={result.event} deadline={result.deadline} "
                    f"refresh_at={result.refresh_at} forced={result.forced} "
                    f"hermes={result.hermes_decision_path or result.hermes_error or 'skipped'}",
                    flush=True,
                )
            except SchedulerTickError as exc:
                print(f"[{exc.result.checked_at.isoformat()}] tick={exc.result.status} error={exc.result.error}", flush=True)
            except Exception as exc:  # pragma: no cover - defensive heartbeat
                print(f"[tick] unexpected error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(self.settings.poll_seconds)

    def _telegram_notification_path(self, season_id: str, event: int) -> Path:
        return self.root / "scheduler" / "telegram_notified" / season_id / f"gw{event}.json"

    def _maybe_notify_telegram(
        self, event: int, season_id: str, deadline: datetime, checked_at: datetime,
    ) -> tuple[bool, str | None]:
        try:
            from aifpl.config import telegram_settings

            settings = telegram_settings()
        except ValueError:
            return False, None
        if not settings.enabled:
            return False, None
        if checked_at < deadline - timedelta(minutes=settings.notify_lead_minutes):
            return False, None
        if checked_at >= deadline:
            return False, None
        marker = self._telegram_notification_path(season_id, event)
        if marker.exists():
            return True, None
        try:
            from aifpl.notifier import TelegramNotifier, build_recommendation_message

            message = build_recommendation_message(self.root, event, season_id, deadline)
            TelegramNotifier.from_environment().send_message(message)
        except Exception as exc:
            return False, redact_secrets(f"{type(exc).__name__}: {exc}")
        marker.parent.mkdir(parents=True, exist_ok=True)
        write_immutable(marker, json_bytes({"event": event, "sent_at": checked_at.isoformat()}, pretty=True))
        return True, None

    def _persist_tick(        self, path: Path, schedule: DeadlineStatus,
        status: Literal["not_due", "already_completed", "in_progress", "succeeded", "failed", "missed"],
        forced: bool, refresh_job_path: str | None = None, error: str | None = None,
        hermes_decision_path: str | None = None,
        hermes_error: str | None = None,
    ) -> SchedulerTickResult:
        result = SchedulerTickResult(
            status=status, checked_at=schedule.checked_at, event=schedule.event,
            deadline=schedule.deadline, refresh_at=schedule.refresh_at,
            season_id=schedule.season_id, missed=schedule.missed, forced=forced,
            refresh_job_path=refresh_job_path, error=error, output_path=str(path),
            hermes_decision_path=hermes_decision_path,
            hermes_error=hermes_error,
        )
        write_immutable(path, json_bytes(result.model_dump(mode="json"), pretty=True))
        return result

    def _persist_discovery_failure(
        self, checked_at: datetime, forced: bool, exc: Exception,
    ) -> SchedulerTickResult:
        path = self._tick_path(checked_at)
        result = SchedulerTickResult(
            status="discovery_failed", checked_at=checked_at, event=None,
            deadline=None, refresh_at=None, season_id=None, missed=None, forced=forced,
            error=redact_secrets(f"{type(exc).__name__}: {exc}"), output_path=str(path),
        )
        write_immutable(path, json_bytes(result.model_dump(mode="json"), pretty=True))
        return result

    def _claim(self, schedule: DeadlineStatus) -> tuple[Path, str] | None:
        path = self._lock_path(schedule.season_id, schedule.event)
        path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        guard = os.open(path.with_suffix(".guard"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            flock(guard, LOCK_EX)
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    age = time.time() - path.stat().st_mtime
                except FileNotFoundError:
                    age = CLAIM_LEASE_SECONDS + 1
                if path.exists() and age <= CLAIM_LEASE_SECONDS:
                    return None
                path.unlink(missing_ok=True)
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"claimed_at": datetime.now(timezone.utc).isoformat(), "token": token}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            return path, token
        finally:
            flock(guard, LOCK_UN)
            os.close(guard)

    @staticmethod
    def _release_claim(claim: tuple[Path, str]) -> None:
        path, token = claim
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("token") == token:
                path.unlink(missing_ok=True)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def _tick_path(self, checked_at: datetime) -> Path:
        stamp = checked_at.strftime("%Y%m%dT%H%M%S%fZ")
        return self.root / "scheduler" / "ticks" / f"{stamp}-{uuid4().hex}.json"

    def _completion_path(self, season_id: str, event: int) -> Path:
        return self.root / "scheduler" / "completed" / season_id / f"gw{event}.json"

    def _lock_path(self, season_id: str, event: int) -> Path:
        return self.root / "scheduler" / "locks" / season_id / f"gw{event}.json"

    def _hermes_completion_path(self, season_id: str, event: int) -> Path:
        return self.root / "scheduler" / "hermes_completed" / season_id / f"gw{event}.json"

    def _run_hermes_for_event(self, event: int, season_id: str) -> str:
        from aifpl.hermes import HermesManager

        manager = HermesManager(self.root)
        try:
            existing = manager.latest_decision()
            if existing.gameweek == event and existing.season_id in ("", season_id):
                return existing.decision_path
            if existing.season_id in ("", season_id) and existing.gameweek > event:
                raise ValueError(f"Hermes has already advanced to gameweek {existing.gameweek}")
        except FileNotFoundError:
            pass
        result = manager.run(expected_gameweek=event, expected_season_id=season_id)
        if result.decision.gameweek != event:
            raise ValueError(f"Hermes returned gameweek {result.decision.gameweek}, expected {event}")
        return result.decision.decision_path

    @staticmethod
    def _season_id(deadline: datetime) -> str:
        start_year = deadline.year if deadline.month >= 7 else deadline.year - 1
        return f"{start_year}-{str(start_year + 1)[-2:]}"
