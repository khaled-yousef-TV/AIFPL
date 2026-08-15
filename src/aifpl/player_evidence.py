from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx

from aifpl.artifacts import complete_artifact_paths, json_bytes, jsonl_bytes, verify_artifact, verify_lineage, write_immutable, write_manifest
from aifpl.config import http_retry_settings
from aifpl.current import CurrentPlayerCatalogStore
from aifpl.retry import retry_sync
from aifpl.xg_projections import elapsed_gameweeks


EvidenceType = Literal["official_availability", "official_news", "historical_start_rate", "predicted_start", "predicted_lineup", "rotation_assessment", "late_return"]


@dataclass(frozen=True)
class PlayerEvidence:
    provider: str
    source_record_id: str
    player_id: int
    evidence_type: EvidenceType
    categorical_value: str | None
    provider_probability: float | None
    published_at: str | None
    fetched_at: str
    source_url: str | None
    source_class: str
    gameweek: int | None = None
    fixture_id: int | None = None
    season_id: str | None = None
    minutes_multiplier: float | None = None


@dataclass(frozen=True)
class PlayerEvidenceCatalog:
    records: int
    output_path: str
    created_at: datetime
    source_player_catalog: str
    manifest_path: str


class PlayerEvidenceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build(self, player_catalog_path: Path | None = None) -> PlayerEvidenceCatalog:
        player_store = CurrentPlayerCatalogStore(self.root)
        player_path = player_catalog_path or player_store.latest_path()
        players = player_store.load(player_path)
        created_at = datetime.now(timezone.utc)
        fetched_at = created_at.isoformat()
        elapsed = elapsed_gameweeks(self.root, player_path, players)
        records: list[PlayerEvidence] = []
        for player in players:
            records.append(PlayerEvidence(
                provider="official_fpl", source_record_id=f"start-rate:{player.id}", player_id=player.id,
                evidence_type="historical_start_rate", categorical_value=None,
                provider_probability=round(min(1.0, player.starts / elapsed), 6),
                published_at=None, fetched_at=fetched_at, source_url=None, source_class="official_fpl",
            ))
            if player.chance_of_playing_next_round is not None or player.status != "a":
                probability = (
                    player.chance_of_playing_next_round / 100
                    if player.chance_of_playing_next_round is not None else 0.0
                )
                records.append(PlayerEvidence(
                    provider="official_fpl", source_record_id=f"availability:{player.id}", player_id=player.id,
                    evidence_type="official_availability", categorical_value=player.status,
                    provider_probability=probability, published_at=player.news_added, fetched_at=fetched_at,
                    source_url=None, source_class="official_fpl",
                ))
            if player.news:
                records.append(PlayerEvidence(
                    provider="official_fpl", source_record_id=f"news:{player.id}:{player.news_added}", player_id=player.id,
                    evidence_type="official_news", categorical_value=player.news, provider_probability=None,
                    published_at=player.news_added, fetched_at=fetched_at, source_url=None, source_class="official_fpl",
                ))
        sources = {"player_catalog": player_path}
        for path, payload, source_url in self._configured_payloads(created_at):
            sources[f"external_evidence_{len(sources)}"] = path
            records.extend(_parse_external(payload, fetched_at, source_url, {player.id for player in players}))
        output_path = self.root / "normalized" / "current" / "player_evidence" / f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}.jsonl"
        write_immutable(output_path, jsonl_bytes(records))
        manifest_path = write_manifest(
            self.root, output_path, artifact_type="player_evidence", created_at=fetched_at,
            record_count=len(records), sources=sources,
            parameters={"historical_gameweeks": elapsed},
        )
        return PlayerEvidenceCatalog(len(records), str(output_path), created_at, str(player_path), str(manifest_path))

    def latest_path(self) -> Path:
        directory = self.root / "normalized" / "current" / "player_evidence"
        files = complete_artifact_paths(sorted(directory.glob("*.jsonl"))) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No player evidence exists; run build-player-evidence first")
        return files[-1]

    def load(self, path: Path, player_catalog_path: Path | None = None) -> list[PlayerEvidence]:
        verify_artifact(self.root, path)
        if player_catalog_path is not None:
            verify_lineage(self.root, path, {"player_catalog": player_catalog_path})
        return [PlayerEvidence(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]

    def latest(self, player_catalog_path: Path | None = None) -> list[PlayerEvidence]:
        return self.load(self.latest_path(), player_catalog_path)

    def _configured_payloads(self, created_at: datetime) -> list[tuple[Path, object, str | None]]:
        payloads: list[tuple[Path, object, str | None]] = []
        configured_file = os.environ.get("AIFPL_PLAYER_EVIDENCE_FILE")
        if configured_file:
            path = Path(configured_file).expanduser()
            payloads.append((path, json.loads(path.read_text(encoding="utf-8")), None))
        urls = [url.strip() for url in os.environ.get("AIFPL_PLAYER_EVIDENCE_URLS", "").split(",") if url.strip()]
        token = os.environ.get("AIFPL_EVIDENCE_BEARER_TOKEN")
        for url in urls:
            def request() -> httpx.Response:
                response = httpx.get(url, timeout=20, headers={"Authorization": f"Bearer {token}"} if token else {})
                response.raise_for_status()
                return response
            response = retry_sync(request, http_retry_settings())
            payload = response.json()
            source_id = hashlib.sha256(url.encode()).hexdigest()[:16]
            path = self.root / "raw" / "evidence" / source_id / f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
            write_immutable(path, json_bytes({"source_url": url, "fetched_at": created_at.isoformat(), "payload": payload}, pretty=True))
            payloads.append((path, payload, url))
        return payloads


def predicted_start_probabilities(
    records: list[PlayerEvidence], gameweek: int, season_id: str, as_of: datetime,
    max_age_hours: float = 168,
) -> dict[tuple[int, int | None], float]:
    priorities = {"official_club": 3, "named_reporter": 2, "aggregator": 1}
    if as_of.tzinfo is None:
        raise ValueError("Evidence cutoff must be timezone-aware")
    selected: dict[tuple[int, int | None], tuple[int, datetime, float]] = {}
    for record in records:
        if record.evidence_type not in ("predicted_start", "predicted_lineup") or record.provider_probability is None:
            continue
        if record.gameweek != gameweek or record.season_id != season_id or record.published_at is None:
            continue
        published = datetime.fromisoformat(record.published_at.replace("Z", "+00:00"))
        if published.tzinfo is None:
            raise ValueError("Evidence published_at must be timezone-aware")
        if published > as_of or (as_of - published).total_seconds() > max_age_hours * 3600:
            continue
        key = (record.player_id, record.fixture_id)
        candidate = (priorities.get(record.source_class, 0), published, record.provider_probability)
        if key not in selected or candidate[:2] > selected[key][:2]:
            selected[key] = candidate
    return {key: value[2] for key, value in selected.items()}


def late_return_adjustments(
    records: list[PlayerEvidence], season_id: str, as_of: datetime, max_age_hours: float = 168,
) -> dict[tuple[int, int], tuple[float, float]]:
    """Per-player, per-gameweek (start probability, minutes multiplier) for tournament/late-return players."""
    if as_of.tzinfo is None:
        raise ValueError("Evidence cutoff must be timezone-aware")
    priorities = {"official_club": 3, "named_reporter": 2, "aggregator": 1}
    selected: dict[tuple[int, int], tuple[int, datetime, float, float]] = {}
    for record in records:
        if record.evidence_type != "late_return" or record.provider_probability is None:
            continue
        if record.gameweek is None or record.season_id != season_id or record.published_at is None:
            continue
        published = datetime.fromisoformat(record.published_at.replace("Z", "+00:00"))
        if published.tzinfo is None:
            raise ValueError("Evidence published_at must be timezone-aware")
        if published > as_of or (as_of - published).total_seconds() > max_age_hours * 3600:
            continue
        key = (record.player_id, record.gameweek)
        multiplier = record.minutes_multiplier if record.minutes_multiplier is not None else 1.0
        candidate = (priorities.get(record.source_class, 0), published, record.provider_probability, multiplier)
        if key not in selected or candidate[:2] > selected[key][:2]:
            selected[key] = candidate
    return {key: (probability, multiplier) for key, (_, _, probability, multiplier) in selected.items()}


def _parse_external(payload: object, fetched_at: str, source_url: str | None, player_ids: set[int]) -> list[PlayerEvidence]:
    if not isinstance(payload, list):
        raise ValueError("External player evidence must be a JSON list")
    records: list[PlayerEvidence] = []
    allowed_types = {"official_availability", "official_news", "historical_start_rate", "predicted_start", "predicted_lineup", "rotation_assessment", "late_return"}
    fetched_time = datetime.fromisoformat(fetched_at)
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"External evidence record {index} must be an object")
        player_id = int(item["player_id"])
        if player_id not in player_ids:
            raise ValueError(f"External evidence references unknown player {player_id}")
        probability = item.get("provider_probability")
        if probability is not None and not 0 <= float(probability) <= 1:
            raise ValueError("provider_probability must be within 0..1")
        evidence_type = str(item["evidence_type"])
        if evidence_type not in allowed_types:
            raise ValueError(f"Unsupported evidence_type: {evidence_type}")
        published_at = item.get("published_at")
        gameweek = int(item["gameweek"]) if item.get("gameweek") is not None else None
        fixture_id = int(item["fixture_id"]) if item.get("fixture_id") is not None else None
        season_id = str(item["season_id"]) if item.get("season_id") is not None else None
        minutes_multiplier = item.get("minutes_multiplier")
        if minutes_multiplier is not None and not 0 < float(minutes_multiplier) <= 1.5:
            raise ValueError("minutes_multiplier must be within 0..1.5")
        if evidence_type in ("predicted_start", "predicted_lineup", "late_return") and (published_at is None or gameweek is None or season_id is None):
            raise ValueError(f"{evidence_type} evidence requires published_at, gameweek, and season_id")
        if published_at:
            published_time = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
            if published_time.tzinfo is None:
                raise ValueError("Evidence published_at must be timezone-aware")
            if published_time > fetched_time:
                raise ValueError("Evidence published_at cannot be later than fetched_at")
        records.append(PlayerEvidence(
            provider=str(item["provider"]), source_record_id=str(item["source_record_id"]),
            player_id=player_id, evidence_type=evidence_type,
            categorical_value=item.get("categorical_value"),
            provider_probability=float(probability) if probability is not None else None,
            published_at=published_at, fetched_at=fetched_at,
            source_url=item.get("source_url") or source_url, source_class=str(item["source_class"]),
            gameweek=gameweek, fixture_id=fixture_id,
            season_id=season_id,
            minutes_multiplier=float(minutes_multiplier) if minutes_multiplier is not None else None,
        ))
    return records
