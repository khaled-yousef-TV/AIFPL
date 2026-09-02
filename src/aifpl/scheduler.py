from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from aifpl.artifacts import json_bytes, write_immutable
from aifpl.config import SchedulerSettings, scheduler_settings
from aifpl.refresh import CurrentDataRefreshJob, RefreshJobError
from aifpl.scoring import CompletedDecisionScorer
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
    scored_decision_paths: list[str] = Field(default_factory=list)
    scoring_error: str | None = None
    telegram_notified: bool | None = None
    telegram_error: str | None = None
    notification_update_for: str | None = None
    error: str | None = None
    output_path: str


class SchedulerTickError(RuntimeError):
    def __init__(self, result: SchedulerTickResult) -> None:
        super().__init__(result.error or "Scheduled refresh failed")
        self.result = result


class DeadlineScheduler:
    def __init__(
        self, root: Path, refresh_job: CurrentDataRefreshJob | None = None,
        settings: SchedulerSettings | None = None, completed_scorer: CompletedDecisionScorer | None = None,
    ) -> None:
        self.root = root
        self.refresh_job = refresh_job or CurrentDataRefreshJob(root)
        self.settings = settings or scheduler_settings()
        self.completed_scorer = completed_scorer or CompletedDecisionScorer(root)
        self._hermes_deadline: datetime | None = None
        self._hermes_deadline_clock: datetime | None = None

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
        refresh_at = self._refresh_at(deadline)
        season_id = self._season_id(deadline)
        return DeadlineStatus(
            checked_at=now, event=event, deadline=deadline, refresh_at=refresh_at,
            due=now >= refresh_at,
            completed=self._completion_exists(season_id, event),
            missed=now >= deadline,
            season_id=season_id,
            source_snapshot=str(source_path),
        )

    def tick(self, checked_at: datetime | None = None, force: bool = False) -> SchedulerTickResult:
        result = self._tick_inner(checked_at, force)
        if (
            result.event is not None
            and result.deadline is not None
            and result.status in ("succeeded", "already_completed")
            and result.hermes_error is None
        ):
            notified, notify_error = self._maybe_notify_telegram(
                result.event, result.season_id or "", result.deadline,
                checked_at if checked_at is not None else datetime.now(timezone.utc),
                decision_path=result.hermes_decision_path,
            )
            result = result.model_copy(update={"telegram_notified": notified, "telegram_error": notify_error})
            result = self._persist_notification_update(result)
        return result

    def _tick_inner(self, checked_at: datetime | None = None, force: bool = False) -> SchedulerTickResult:
        try:
            schedule = self.status(checked_at)
        except Exception as exc:
            now = checked_at if checked_at is not None and checked_at.tzinfo is not None else datetime.now(timezone.utc)
            result = self._persist_discovery_failure(now.astimezone(timezone.utc), force, exc)
            raise SchedulerTickError(result) from exc
        self._hermes_deadline = schedule.deadline
        self._hermes_deadline_clock = checked_at
        output_path = self._tick_path(schedule.checked_at)
        scored_decision_paths, scoring_error = self._score_completed_decisions(schedule.season_id)
        scorecard_kwargs = {
            "scored_decision_paths": scored_decision_paths,
            "scoring_error": scoring_error,
        }
        if not schedule.completed and not schedule.missed and not self._before_deadline(schedule.deadline, checked_at):
            return self._persist_tick(output_path, schedule, "missed", force, **scorecard_kwargs)
        if schedule.completed:
            if (
                not schedule.missed
                and self._before_deadline(schedule.deadline, checked_at)
                and os.environ.get("AIFPL_HERMES_AUTO_RUN", "false").lower() in ("1", "true", "yes")
                and not self._hermes_completion_exists(schedule.season_id, schedule.event)
            ):
                try:
                    decision_path = self._run_hermes_for_event(schedule.event, schedule.season_id)
                    result = self._persist_tick(
                        output_path, schedule, "already_completed", force,
                        hermes_decision_path=decision_path, **scorecard_kwargs,
                    )
                    write_immutable(
                        self._hermes_completion_write_path(schedule.season_id, schedule.event, schedule.checked_at),
                        json_bytes({"decision_path": decision_path}, pretty=True),
                    )
                    return result
                except Exception as exc:
                    return self._persist_tick(
                        output_path, schedule, "already_completed", force,
                        hermes_error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                        **scorecard_kwargs,
                    )
            return self._persist_tick(output_path, schedule, "already_completed", force, **scorecard_kwargs)
        if schedule.missed:
            return self._persist_tick(output_path, schedule, "missed", force, **scorecard_kwargs)
        if not schedule.due and not force:
            return self._persist_tick(output_path, schedule, "not_due", force, **scorecard_kwargs)
        claim = self._claim(schedule)
        if claim is None:
            status = "already_completed" if self._completion_exists(schedule.season_id, schedule.event) else "in_progress"
            return self._persist_tick(output_path, schedule, status, force, **scorecard_kwargs)
        try:
            if self._completion_exists(schedule.season_id, schedule.event):
                return self._persist_tick(output_path, schedule, "already_completed", force, **scorecard_kwargs)
            end_gameweek = min(38, schedule.event + self.settings.horizon_gameweeks - 1)
            refresh = self.refresh_job.run(schedule.event, end_gameweek, self.settings.budget)
            hermes_decision_path = None
            hermes_error = None
            if not self._before_deadline(schedule.deadline, checked_at):
                result = self._persist_tick(
                    output_path, schedule, "failed", force,
                    refresh_job_path=refresh.output_path,
                    error="Refresh completed at or after the official deadline; Hermes was not run.",
                    **scorecard_kwargs,
                )
                raise SchedulerTickError(result)
            if os.environ.get("AIFPL_HERMES_AUTO_RUN", "false").lower() in ("1", "true", "yes"):
                try:
                    hermes_decision_path = self._run_hermes_for_event(schedule.event, schedule.season_id)
                except Exception as exc:
                    hermes_error = redact_secrets(f"{type(exc).__name__}: {exc}")
            result = self._persist_tick(
                output_path, schedule, "succeeded", force, refresh_job_path=refresh.output_path,
                hermes_decision_path=hermes_decision_path,
                hermes_error=hermes_error,
                **scorecard_kwargs,
            )
            write_immutable(
                self._completion_write_path(schedule.season_id, schedule.event, result.checked_at),
                json_bytes(result.model_dump(mode="json"), pretty=True),
            )
            if hermes_decision_path:
                write_immutable(
                    self._hermes_completion_write_path(schedule.season_id, schedule.event, result.checked_at),
                    json_bytes({"decision_path": hermes_decision_path}, pretty=True),
                )
            return result
        except SchedulerTickError:
            raise
        except Exception as exc:
            refresh_path = exc.result.output_path if isinstance(exc, RefreshJobError) else None
            result = self._persist_tick(
                output_path, schedule, "failed", force,
                refresh_job_path=refresh_path, error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                **scorecard_kwargs,
            )
            raise SchedulerTickError(result) from exc
        finally:
            self._release_claim(claim)

    def run_forever(self, stop_event: Event | None = None) -> None:
        while stop_event is None or not stop_event.is_set():
            try:
                result = self.tick()
                print(
                    f"[{result.checked_at.isoformat()}] tick={result.status} "
                    f"event={result.event} deadline={result.deadline} "
                    f"refresh_at={result.refresh_at} forced={result.forced} "
                    f"scorecards={len(result.scored_decision_paths)} "
                    f"hermes={result.hermes_decision_path or result.hermes_error or 'skipped'}",
                    flush=True,
                )
            except SchedulerTickError as exc:
                print(f"[{exc.result.checked_at.isoformat()}] tick={exc.result.status} error={exc.result.error}", flush=True)
            except Exception as exc:  # pragma: no cover - defensive heartbeat
                print(f"[tick] unexpected error: {type(exc).__name__}: {exc}", flush=True)
            if stop_event is None:
                time.sleep(self.settings.poll_seconds)
            else:
                stop_event.wait(self.settings.poll_seconds)

    def _telegram_notification_path(self, season_id: str, event: int) -> Path:
        return self.root / "scheduler" / "telegram_notified" / season_id / f"gw{event}.json"

    def _telegram_decision_receipt_path(self, season_id: str, event: int, decision_path: str) -> Path:
        digest = sha256(decision_path.encode("utf-8")).hexdigest()[:16]
        return self.root / "scheduler" / "telegram_notified" / season_id / f"gw{event}" / f"{digest}.json"

    def _telegram_notification_lock_path(self, season_id: str, event: int) -> Path:
        return self.root / "scheduler" / "telegram_locks" / season_id / f"gw{event}.lock"

    def _telegram_notification_result_path(
        self, season_id: str, event: int, checked_at: datetime,
    ) -> Path:
        stamp = checked_at.strftime("%Y%m%dT%H%M%S%fZ")
        return self.root / "scheduler" / "telegram_results" / season_id / f"gw{event}" / f"{stamp}-{uuid4().hex}.json"

    def _telegram_result_matches(self, season_id: str, event: int, decision_path: str) -> bool:
        directory = self.root / "scheduler" / "telegram_results" / season_id / f"gw{event}"
        if not directory.exists():
            return False
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(result, dict)
                and result.get("status") == "sent"
                and result.get("event") == event
                and result.get("season_id") == season_id
                and result.get("decision_path") == decision_path
            ):
                return True
        return False

    def _maybe_notify_telegram(
        self, event: int, season_id: str, deadline: datetime, checked_at: datetime,
        decision_path: str | None = None,
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

        try:
            from aifpl.notifier import (
                TelegramNotifier, _load_committed_decision, build_recommendation_message,
            )

            decision = _load_committed_decision(
                self.root, event, season_id, decision_path=decision_path,
            )
        except Exception as exc:
            error = redact_secrets(f"{type(exc).__name__}: {exc}")
            self._persist_telegram_result(event, season_id, checked_at, decision_path, "failed", error)
            return False, error
        if decision is None:
            error = "No current committed Hermes decision is available for notification"
            self._persist_telegram_result(event, season_id, checked_at, decision_path, "skipped", error)
            return False, error

        if self._telegram_result_matches(season_id, event, decision.decision_path):
            return True, None
        marker = self._telegram_notification_path(season_id, event)
        if marker.exists():
            try:
                receipt = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                receipt = None
            if (
                isinstance(receipt, dict)
                and receipt.get("status") == "sent"
                and receipt.get("event") == event
                and receipt.get("season_id") == season_id
                and receipt.get("decision_path") == decision.decision_path
            ):
                return True, None

        lock_path = self._telegram_notification_lock_path(season_id, event)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        locked = False
        try:
            try:
                flock(descriptor, LOCK_EX | LOCK_NB)
                locked = True
            except BlockingIOError:
                error = "Another Telegram notification is already in progress"
                self._persist_telegram_result(event, season_id, checked_at, decision.decision_path, "skipped", error)
                return False, error
            # Recheck after acquiring the lock so concurrent ticks cannot send twice.
            if marker.exists():
                try:
                    receipt = json.loads(marker.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    receipt = None
                if (
                    isinstance(receipt, dict)
                    and receipt.get("status") == "sent"
                    and receipt.get("event") == event
                    and receipt.get("season_id") == season_id
                    and receipt.get("decision_path") == decision.decision_path
                ):
                    return True, None
            if self._telegram_result_matches(season_id, event, decision.decision_path):
                return True, None
            current_decision = _load_committed_decision(
                self.root, event, season_id, decision_path=decision.decision_path,
            )
            if current_decision is None:
                error = "Hermes decision changed before notification could be sent"
                self._persist_telegram_result(event, season_id, checked_at, decision.decision_path, "skipped", error)
                return False, error
            decision = current_decision
            message = build_recommendation_message(
                self.root, event, season_id, deadline, decision_path=decision.decision_path,
            )
            TelegramNotifier.from_environment().send_message(message)

            result_path = self._persist_telegram_result(
                event, season_id, checked_at, decision.decision_path, "sent", None,
            )
            receipt_path = marker if not marker.exists() else self._telegram_decision_receipt_path(
                season_id, event, decision.decision_path,
            )
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            write_immutable(
                receipt_path,
                json_bytes({
                    "status": "sent", "event": event, "season_id": season_id,
                    "decision_path": decision.decision_path, "sent_at": checked_at.isoformat(),
                    "result_path": str(result_path),
                }, pretty=True),
            )
            return True, None
        except Exception as exc:
            error = redact_secrets(f"{type(exc).__name__}: {exc}")
            self._persist_telegram_result(event, season_id, checked_at, decision.decision_path, "failed", error)
            return False, error
        finally:
            if locked:
                flock(descriptor, LOCK_UN)
            os.close(descriptor)

    def _persist_telegram_result(
        self, event: int, season_id: str, checked_at: datetime, decision_path: str | None,
        status: str, error: str | None,
    ) -> Path:
        path = self._telegram_notification_result_path(season_id, event, checked_at)
        write_immutable(
            path,
            json_bytes({
                "status": status, "event": event, "season_id": season_id,
                "decision_path": decision_path, "attempted_at": checked_at.isoformat(),
                "error": error,
            }, pretty=True),
        )
        return path

    def _persist_tick(        self, path: Path, schedule: DeadlineStatus,
        status: Literal["not_due", "already_completed", "in_progress", "succeeded", "failed", "missed"],
        forced: bool, refresh_job_path: str | None = None, error: str | None = None,
        hermes_decision_path: str | None = None,
        hermes_error: str | None = None,
        scored_decision_paths: list[str] | None = None,
        scoring_error: str | None = None,
    ) -> SchedulerTickResult:
        result = SchedulerTickResult(
            status=status, checked_at=schedule.checked_at, event=schedule.event,
            deadline=schedule.deadline, refresh_at=schedule.refresh_at,
            season_id=schedule.season_id, missed=schedule.missed, forced=forced,
            refresh_job_path=refresh_job_path, error=error, output_path=str(path),
            hermes_decision_path=hermes_decision_path,
            hermes_error=hermes_error,
            scored_decision_paths=scored_decision_paths or [],
            scoring_error=scoring_error,
        )
        write_immutable(path, json_bytes(result.model_dump(mode="json"), pretty=True))
        return result

    def _persist_notification_update(self, result: SchedulerTickResult) -> SchedulerTickResult:
        stamp = result.checked_at.strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / "scheduler" / "ticks" / f"{stamp}-notification-{uuid4().hex}.json"
        updated = result.model_copy(update={
            "output_path": str(path),
            "notification_update_for": result.output_path,
        })
        write_immutable(path, json_bytes(updated.model_dump(mode="json"), pretty=True))
        return updated

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

    def _score_completed_decisions(self, season_id: str) -> tuple[list[str], str | None]:
        try:
            return self.completed_scorer.score_pending(season_id), None
        except Exception as exc:
            return [], redact_secrets(f"{type(exc).__name__}: {exc}")

    def _refresh_at(self, deadline: datetime) -> datetime:
        if self.settings.release_time is None:
            return deadline - timedelta(minutes=self.settings.lead_minutes)
        release_timezone = ZoneInfo(self.settings.timezone)
        local_deadline = deadline.astimezone(release_timezone)
        local_release = datetime.combine(local_deadline.date(), self.settings.release_time, tzinfo=release_timezone)
        refresh_at = local_release.astimezone(timezone.utc)
        if refresh_at >= deadline:
            return deadline - timedelta(minutes=self.settings.lead_minutes)
        return refresh_at

    def _completion_path(self, season_id: str, event: int) -> Path:
        return self.root / "scheduler" / "completed" / season_id / f"gw{event}.json"

    def _completion_exists(self, season_id: str, event: int) -> bool:
        for path in self._completion_candidates(season_id, event):
            if self._completion_valid(path, season_id, event):
                return True
        return False

    def _completion_write_path(self, season_id: str, event: int, checked_at: datetime) -> Path:
        path = self._completion_path(season_id, event)
        if not path.exists() or not self._completion_valid(path, season_id, event):
            if not path.exists():
                return path
            stamp = checked_at.strftime("%Y%m%dT%H%M%S%fZ")
            return path.parent / f"gw{event}-{stamp}-{uuid4().hex}.json"
        return path

    def _completion_candidates(self, season_id: str, event: int) -> list[Path]:
        path = self._completion_path(season_id, event)
        siblings = sorted(path.parent.glob(f"gw{event}-*.json"), reverse=True) if path.parent.exists() else []
        return [path, *siblings]

    def _completion_valid(self, path: Path, season_id: str, event: int) -> bool:
        try:
            result = SchedulerTickResult.model_validate_json(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return False
        except ValueError:
            return False
        if result.status not in {"succeeded", "already_completed"}:
            return False
        if result.season_id != season_id or result.event != event:
            return False
        output = Path(result.output_path)
        if not output.is_absolute():
            output = self.root / output
        return output.is_file()

    @staticmethod
    def _before_deadline(deadline: datetime, checked_at: datetime | None) -> bool:
        now = checked_at if checked_at is not None else datetime.now(timezone.utc)
        return now.astimezone(timezone.utc) < deadline.astimezone(timezone.utc)

    def _lock_path(self, season_id: str, event: int) -> Path:
        return self.root / "scheduler" / "locks" / season_id / f"gw{event}.json"

    def _hermes_completion_path(self, season_id: str, event: int) -> Path:
        return self.root / "scheduler" / "hermes_completed" / season_id / f"gw{event}.json"

    def _hermes_completion_exists(self, season_id: str, event: int) -> bool:
        from aifpl.hermes import HermesDecision

        for path in self._hermes_completion_candidates(season_id, event):
            try:
                marker = json.loads(path.read_text(encoding="utf-8"))
                reference = marker.get("decision_path") if isinstance(marker, dict) else None
                if not isinstance(reference, str):
                    continue
                decision_path = Path(reference)
                if not decision_path.is_absolute():
                    decision_path = self.root / decision_path
                decision = HermesDecision.model_validate_json(decision_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
                continue
            if decision.season_id == season_id and decision.gameweek == event:
                return True
        return False

    def _hermes_completion_candidates(self, season_id: str, event: int) -> list[Path]:
        path = self._hermes_completion_path(season_id, event)
        siblings = sorted(path.parent.glob(f"gw{event}-*.json"), reverse=True) if path.parent.exists() else []
        return [path, *siblings]

    def _hermes_completion_write_path(self, season_id: str, event: int, checked_at: datetime) -> Path:
        path = self._hermes_completion_path(season_id, event)
        if not path.exists() or not self._hermes_completion_exists(season_id, event):
            if not path.exists():
                return path
            stamp = checked_at.strftime("%Y%m%dT%H%M%S%fZ")
            return path.parent / f"gw{event}-{stamp}-{uuid4().hex}.json"
        return path

    def _run_hermes_for_event(self, event: int, season_id: str) -> str:
        from aifpl.hermes import HermesManager

        manager = HermesManager(self.root)
        try:
            existing = manager.latest_decision()
            if existing.gameweek == event and existing.season_id == season_id:
                return existing.decision_path
            if existing.season_id == season_id and existing.gameweek > event:
                raise ValueError(f"Hermes has already advanced to gameweek {existing.gameweek}")
        except FileNotFoundError:
            pass
        result = manager.run(
            expected_gameweek=event,
            expected_season_id=season_id,
            deadline=self._hermes_deadline,
            deadline_clock=self._hermes_deadline_clock,
        )
        if result.decision.gameweek != event or result.decision.season_id != season_id:
            raise ValueError(
                f"Hermes returned {result.decision.season_id}/GW{result.decision.gameweek}, "
                f"expected {season_id}/GW{event}"
            )
        return result.decision.decision_path

    @staticmethod
    def _season_id(deadline: datetime) -> str:
        start_year = deadline.year if deadline.month >= 7 else deadline.year - 1
        return f"{start_year}-{str(start_year + 1)[-2:]}"
