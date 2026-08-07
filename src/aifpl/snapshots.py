from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aifpl.fpl import (
    BootstrapSummary,
    EventLiveSummary,
    FixtureSummary,
    summarize_bootstrap,
    summarize_event_live,
    summarize_fixtures,
)


class SnapshotNotFoundError(FileNotFoundError):
    pass


class SnapshotStore:
    """Append-only raw-source storage used to make later backtests reproducible."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def bootstrap_dir(self) -> Path:
        return self.root / "raw" / "fpl" / "bootstrap"

    @property
    def fixtures_dir(self) -> Path:
        return self.root / "raw" / "fpl" / "fixtures"

    @property
    def events_dir(self) -> Path:
        return self.root / "raw" / "fpl" / "events"

    def save_bootstrap(self, payload: dict[str, Any], fetched_at: datetime | None = None) -> tuple[Path, BootstrapSummary]:
        retrieved = fetched_at or datetime.now(timezone.utc)
        if retrieved.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        summary = summarize_bootstrap(payload, retrieved)
        destination = self._save("bootstrap", self.bootstrap_dir, payload, retrieved)
        return destination, summary

    def latest_bootstrap(self) -> tuple[Path, BootstrapSummary]:
        latest, document = self._latest_document(self.bootstrap_dir, "No FPL bootstrap snapshots have been saved yet")
        return latest, summarize_bootstrap(document["payload"], self._fetched_at(document))

    def bootstrap_before(self, cutoff: datetime) -> tuple[Path, BootstrapSummary]:
        if cutoff.tzinfo is None:
            raise ValueError("cutoff must be timezone-aware")
        path, document = self._document_before(self.bootstrap_dir, cutoff, "No FPL bootstrap snapshot exists at or before that time")
        return path, summarize_bootstrap(document["payload"], self._fetched_at(document))

    def save_fixtures(self, payload: list[dict[str, Any]], fetched_at: datetime | None = None) -> tuple[Path, FixtureSummary]:
        retrieved = fetched_at or datetime.now(timezone.utc)
        summary = summarize_fixtures(payload, retrieved)
        return self._save("fixtures", self.fixtures_dir, payload, retrieved), summary

    def save_event_live(
        self, event: int, payload: dict[str, Any], fetched_at: datetime | None = None
    ) -> tuple[Path, EventLiveSummary]:
        retrieved = fetched_at or datetime.now(timezone.utc)
        summary = summarize_event_live(event, payload, retrieved)
        directory = self.events_dir / str(event)
        return self._save(f"event-{event}-live", directory, payload, retrieved), summary

    @staticmethod
    def _fetched_at(document: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(document["metadata"]["fetched_at"])

    def _save(self, source: str, directory: Path, payload: Any, retrieved: datetime) -> Path:
        if retrieved.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        stamped = retrieved.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f")
        destination = directory / f"{stamped}.json"
        directory.mkdir(parents=True, exist_ok=True)
        document = {"metadata": {"source": f"official-fpl-api:{source}", "fetched_at": retrieved.isoformat()}, "payload": payload}
        destination.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        return destination

    def _latest_document(self, directory: Path, error: str) -> tuple[Path, dict[str, Any]]:
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise SnapshotNotFoundError(error)
        path = files[-1]
        return path, json.loads(path.read_text(encoding="utf-8"))

    def _document_before(self, directory: Path, cutoff: datetime, error: str) -> tuple[Path, dict[str, Any]]:
        candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
        for path in directory.glob("*.json") if directory.exists() else []:
            document = json.loads(path.read_text(encoding="utf-8"))
            retrieved = self._fetched_at(document)
            if retrieved <= cutoff:
                candidates.append((retrieved, path, document))
        if not candidates:
            raise SnapshotNotFoundError(error)
        _, path, document = max(candidates, key=lambda item: item[0])
        return path, document
