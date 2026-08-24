from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal

from aifpl.artifacts import (
    complete_artifact_paths,
    json_bytes,
    jsonl_bytes,
    resolve_artifact_ref,
    sha256_path,
    verify_artifact,
    write_immutable,
    write_manifest,
)
from aifpl.config import LiveCalibrationSettings, live_calibration_settings
from aifpl.odds_projections import OddsAdjustedGameweekProjection, OddsProjectionStore
from aifpl.snapshots import SnapshotStore


LIVE_CALIBRATION_METHOD = "rolling_affine_v1"
OUTCOME_COVERAGE_FLOOR = 0.95
MAX_INTERCEPT_PER_FIXTURE = 0.25
MAX_SLOPE_DELTA = 0.15
MAX_ADJUSTMENT_PER_FIXTURE = 0.5


@dataclass(frozen=True)
class LiveCalibrationOutcome:
    season_id: str
    gameweek: int
    player_id: int
    fixture_count: int
    predicted_points: float
    actual_points: float
    methodology: str
    model_signature: str


@dataclass(frozen=True)
class LiveCalibrationOutcomeCatalog:
    season_id: str
    gameweek: int
    methodology: str
    model_signature: str
    observations: int
    coverage: float
    output_path: str
    manifest_path: str


@dataclass(frozen=True)
class LiveCalibrationProfile:
    season_id: str
    methodology: str
    model_signature: str
    status: Literal["warming_up", "active"]
    policy_version: str
    gameweeks: list[int]
    latest_gameweek: int
    observations: int
    raw_slope: float
    raw_intercept: float
    slope: float
    intercept: float
    maturity: float
    output_path: str
    created_at: datetime


@dataclass(frozen=True)
class CalibratedOddsCatalog:
    catalog_id: str
    raw_catalog_id: str
    rows: list[OddsAdjustedGameweekProjection]
    profile: LiveCalibrationProfile | None


class LiveCalibrationStore:
    def __init__(self, root: Path, settings: LiveCalibrationSettings | None = None) -> None:
        self.root = root
        self.settings = settings or live_calibration_settings()

    def record_outcomes(
        self,
        season_id: str,
        gameweek: int,
        catalog_id: str,
        bootstrap_path: Path,
        event_path: Path,
        decision_path: Path,
        decision_created_at: datetime,
    ) -> LiveCalibrationOutcomeCatalog | None:
        if Path(catalog_id).name != catalog_id or not catalog_id.endswith(".jsonl"):
            return None
        applied_catalog_path = self.root / "normalized" / "current" / "odds_projections" / catalog_id
        catalog_path, raw_catalog_id = self._raw_catalog(applied_catalog_path)
        rows = OddsProjectionStore(self.root).latest(raw_catalog_id)
        gameweek_rows = [row for row in rows if row.gameweek == gameweek]
        if not gameweek_rows:
            return None
        if not self._has_full_odds_coverage(catalog_path, gameweek):
            return None
        methodologies = {row.methodology for row in gameweek_rows}
        if len(methodologies) != 1:
            raise ValueError("Calibration source catalog must contain one projection methodology")
        methodology = methodologies.pop()
        model_signature = self._model_signature(catalog_path)
        self._require_final_gameweek(bootstrap_path, gameweek)
        self._require_predeadline_inputs(catalog_path, applied_catalog_path, decision_path, decision_created_at, bootstrap_path, gameweek)
        actuals = self._actual_points(event_path)
        eligible = [row for row in gameweek_rows if row.fixture_count > 0]
        matched = [row for row in eligible if row.player_id in actuals]
        if not eligible:
            raise ValueError("Calibration source catalog has no players with fixtures")
        coverage = len(matched) / len(eligible)
        if coverage < OUTCOME_COVERAGE_FLOOR:
            raise ValueError(f"Calibration outcome coverage {coverage:.1%} is below {OUTCOME_COVERAGE_FLOOR:.0%}")
        outcomes = [
            LiveCalibrationOutcome(
                season_id=season_id,
                gameweek=gameweek,
                player_id=row.player_id,
                fixture_count=row.fixture_count,
                predicted_points=row.projected_points,
                actual_points=actuals[row.player_id],
                methodology=methodology,
                model_signature=model_signature,
            )
            for row in matched
        ]
        catalog_hash = sha256_path(catalog_path)[:12]
        output_path = self._outcomes_dir(season_id, methodology, model_signature) / f"gw{gameweek}.{catalog_hash}.jsonl"
        manifest_path = output_path.with_suffix(".manifest.json")
        if not output_path.exists():
            write_immutable(output_path, jsonl_bytes(outcomes))
            write_manifest(
                self.root,
                output_path,
                artifact_type="live_calibration_outcomes",
                created_at=datetime.now(timezone.utc).isoformat(),
                record_count=len(outcomes),
                sources={
                    "raw_projection_catalog": catalog_path,
                    "applied_projection_catalog": applied_catalog_path,
                    "bootstrap_snapshot": bootstrap_path,
                    "event_snapshot": event_path,
                    "decision": decision_path,
                },
                methodology=methodology,
                parameters={
                    "season_id": season_id,
                    "gameweek": gameweek,
                    "coverage": round(coverage, 6),
                    "raw_catalog_id": raw_catalog_id,
                    "applied_catalog_id": catalog_id,
                    "model_signature": model_signature,
                },
            )
        elif not manifest_path.exists():
            if output_path.read_bytes() != jsonl_bytes(outcomes):
                raise ValueError(f"Existing live calibration outcomes disagree with final source data: {output_path}")
            write_manifest(
                self.root,
                output_path,
                artifact_type="live_calibration_outcomes",
                created_at=datetime.now(timezone.utc).isoformat(),
                record_count=len(outcomes),
                sources={
                    "raw_projection_catalog": catalog_path,
                    "applied_projection_catalog": applied_catalog_path,
                    "bootstrap_snapshot": bootstrap_path,
                    "event_snapshot": event_path,
                    "decision": decision_path,
                },
                methodology=methodology,
                parameters={
                    "season_id": season_id,
                    "gameweek": gameweek,
                    "coverage": round(coverage, 6),
                    "raw_catalog_id": raw_catalog_id,
                    "applied_catalog_id": catalog_id,
                    "model_signature": model_signature,
                },
            )
        else:
            verify_artifact(self.root, output_path, require_manifest=True)
        return LiveCalibrationOutcomeCatalog(
            season_id=season_id,
            gameweek=gameweek,
            methodology=methodology,
            model_signature=model_signature,
            observations=len(outcomes),
            coverage=round(coverage, 6),
            output_path=str(output_path),
            manifest_path=str(manifest_path),
        )

    def needs_outcomes(self, season_id: str, gameweek: int, catalog_id: str) -> bool:
        if Path(catalog_id).name != catalog_id or not catalog_id.endswith(".jsonl"):
            return False
        try:
            applied_path = self.root / "normalized" / "current" / "odds_projections" / catalog_id
            raw_path, _ = self._raw_catalog(applied_path)
            rows = OddsProjectionStore(self.root).latest(raw_path.name)
            methodologies = {row.methodology for row in rows if row.gameweek == gameweek}
            if len(methodologies) != 1 or not self._has_full_odds_coverage(raw_path, gameweek):
                return False
            methodology = methodologies.pop()
            output_path = self._outcomes_dir(season_id, methodology, self._model_signature(raw_path)) / f"gw{gameweek}.{sha256_path(raw_path)[:12]}.jsonl"
            return not output_path.exists() or not output_path.with_suffix(".manifest.json").exists()
        except (FileNotFoundError, OSError, ValueError):
            return False

    def needs_profile(self, season_id: str, gameweek: int, catalog_id: str) -> bool:
        if Path(catalog_id).name != catalog_id or not catalog_id.endswith(".jsonl"):
            return False
        try:
            applied_path = self.root / "normalized" / "current" / "odds_projections" / catalog_id
            raw_path, _ = self._raw_catalog(applied_path)
            rows = OddsProjectionStore(self.root).latest(raw_path.name)
            methodologies = {row.methodology for row in rows if row.gameweek == gameweek}
            if len(methodologies) != 1 or not self._has_full_odds_coverage(raw_path, gameweek):
                return False
            methodology = methodologies.pop()
            model_signature = self._model_signature(raw_path)
            outcome_path = self._outcomes_dir(season_id, methodology, model_signature) / f"gw{gameweek}.{sha256_path(raw_path)[:12]}.jsonl"
            if not outcome_path.exists() or not outcome_path.with_suffix(".manifest.json").exists():
                return False
            profile = self.latest_profile(season_id, methodology, model_signature)
            return profile is None or profile.latest_gameweek < gameweek
        except (FileNotFoundError, OSError, ValueError):
            return False

    def build_profile(self, season_id: str, methodology: str, model_signature: str) -> LiveCalibrationProfile:
        by_gameweek: dict[int, tuple[Path, list[LiveCalibrationOutcome]]] = {}
        for path in self._outcome_paths(season_id, methodology, model_signature):
            rows = self._load_outcomes(path)
            if not rows:
                continue
            gameweeks = {row.gameweek for row in rows}
            methods = {row.methodology for row in rows}
            signatures = {row.model_signature for row in rows}
            if len(gameweeks) != 1 or methods != {methodology} or signatures != {model_signature}:
                raise ValueError(f"Invalid live calibration outcome catalog: {path}")
            gameweek = gameweeks.pop()
            if gameweek in by_gameweek:
                raise ValueError(f"Multiple live calibration outcome catalogs exist for GW{gameweek}")
            by_gameweek[gameweek] = (path, rows)
        if not by_gameweek:
            raise FileNotFoundError("No live calibration outcomes exist")

        gameweeks = sorted(by_gameweek)[-self.settings.window_gameweeks:]
        latest_gameweek = gameweeks[-1]
        weighted_rows: list[tuple[LiveCalibrationOutcome, float]] = []
        for gameweek in gameweeks:
            rows = by_gameweek[gameweek][1]
            weight = self.settings.recency_decay ** (latest_gameweek - gameweek) / len(rows)
            weighted_rows.extend((row, weight) for row in rows)
        observations = len(weighted_rows)
        raw_slope, raw_intercept = _weighted_affine_fit(weighted_rows)
        active = len(gameweeks) >= self.settings.min_gameweeks and observations >= self.settings.min_observations
        maturity = _maturity(gameweeks, observations, self.settings) if active else 0.0
        slope = 1 + _clamp(maturity * (raw_slope - 1), -MAX_SLOPE_DELTA, MAX_SLOPE_DELTA) if active else 1.0
        intercept = _clamp(maturity * raw_intercept, -MAX_INTERCEPT_PER_FIXTURE, MAX_INTERCEPT_PER_FIXTURE) if active else 0.0
        created_at = datetime.now(timezone.utc)
        output_path = self._profiles_dir(season_id, methodology, model_signature) / f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}.{LIVE_CALIBRATION_METHOD}.json"
        profile = LiveCalibrationProfile(
            season_id=season_id,
            methodology=methodology,
            model_signature=model_signature,
            status="active" if active else "warming_up",
            policy_version=LIVE_CALIBRATION_METHOD,
            gameweeks=gameweeks,
            latest_gameweek=latest_gameweek,
            observations=observations,
            raw_slope=round(raw_slope, 6),
            raw_intercept=round(raw_intercept, 6),
            slope=round(slope, 6),
            intercept=round(intercept, 6),
            maturity=round(maturity, 6),
            output_path=str(output_path),
            created_at=created_at,
        )
        write_immutable(output_path, json_bytes(asdict(profile), pretty=True))
        write_manifest(
            self.root,
            output_path,
            artifact_type="live_calibration_profile",
            created_at=created_at.isoformat(),
            record_count=observations,
            sources={f"outcome_gw{gameweek}": by_gameweek[gameweek][0] for gameweek in gameweeks},
            methodology=methodology,
            parameters={
                "status": profile.status,
                "policy_version": LIVE_CALIBRATION_METHOD,
                "model_signature": model_signature,
                "window_gameweeks": self.settings.window_gameweeks,
                "min_gameweeks": self.settings.min_gameweeks,
                "min_observations": self.settings.min_observations,
                "recency_decay": self.settings.recency_decay,
            },
        )
        return profile

    def latest_profile(self, season_id: str, methodology: str, model_signature: str) -> LiveCalibrationProfile | None:
        files = sorted(
            path for path in self._profiles_dir(season_id, methodology, model_signature).glob("*.json")
            if not path.name.endswith(".manifest.json") and path.with_suffix(".manifest.json").exists()
        )
        for path in reversed(files):
            verify_artifact(self.root, path, require_manifest=True)
            document = json.loads(path.read_text(encoding="utf-8"))
            document["created_at"] = datetime.fromisoformat(document["created_at"])
            profile = LiveCalibrationProfile(**document)
            if profile.season_id == season_id and profile.methodology == methodology and profile.model_signature == model_signature:
                return profile
        return None

    def apply(
        self,
        rows: list[OddsAdjustedGameweekProjection],
        profile: LiveCalibrationProfile,
    ) -> list[OddsAdjustedGameweekProjection]:
        if profile.status != "active":
            return rows
        adjusted: list[OddsAdjustedGameweekProjection] = []
        for row in rows:
            if row.fixture_count <= 0:
                adjusted.append(replace(row, methodology=f"{row.methodology}.{LIVE_CALIBRATION_METHOD}"))
                continue
            per_fixture = row.projected_points / row.fixture_count
            candidate = row.fixture_count * max(0.0, profile.intercept + profile.slope * per_fixture)
            delta = _clamp(
                candidate - row.projected_points,
                -MAX_ADJUSTMENT_PER_FIXTURE * row.fixture_count,
                MAX_ADJUSTMENT_PER_FIXTURE * row.fixture_count,
            )
            adjusted.append(replace(
                row,
                projected_points=round(max(0.0, row.projected_points + delta), 4),
                methodology=f"{row.methodology}.{LIVE_CALIBRATION_METHOD}",
            ))
        return adjusted

    def materialize_catalog(
        self,
        catalog_id: str,
        season_id: str,
    ) -> CalibratedOddsCatalog:
        applied_path = self.root / "normalized" / "current" / "odds_projections" / catalog_id
        raw_path, raw_catalog_id = self._raw_catalog(applied_path)
        if self._is_calibrated_catalog(applied_path):
            return CalibratedOddsCatalog(
                applied_path.name,
                raw_catalog_id,
                OddsProjectionStore(self.root).latest(applied_path.name),
                self._profile_for_calibrated_catalog(applied_path),
            )
        rows = OddsProjectionStore(self.root).latest(raw_catalog_id)
        methodologies = {row.methodology for row in rows}
        if len(methodologies) != 1:
            raise ValueError("Odds projection catalog must contain one methodology")
        methodology = methodologies.pop()
        profile = self.latest_profile(season_id, methodology, self._model_signature(raw_path))
        if profile is None:
            return CalibratedOddsCatalog(raw_catalog_id, raw_catalog_id, rows, None)
        if profile.latest_gameweek >= min(row.gameweek for row in rows):
            return CalibratedOddsCatalog(raw_catalog_id, raw_catalog_id, rows, None)
        if profile.status != "active":
            return CalibratedOddsCatalog(raw_catalog_id, raw_catalog_id, rows, profile)

        profile_path = Path(profile.output_path)
        profile_hash = sha256_path(profile_path)[:12]
        output_path = raw_path.parent / f"{raw_path.stem}.{profile_hash}.{LIVE_CALIBRATION_METHOD}.jsonl"
        if not output_path.exists():
            adjusted = self.apply(rows, profile)
            write_immutable(output_path, jsonl_bytes(adjusted))
            write_manifest(
                self.root,
                output_path,
                artifact_type="calibrated_odds_projections",
                created_at=datetime.now(timezone.utc).isoformat(),
                record_count=len(adjusted),
                sources={"raw_projection_catalog": raw_path, "calibration_profile": profile_path},
                methodology=adjusted[0].methodology if adjusted else None,
                parameters={
                    "raw_catalog_id": raw_catalog_id,
                    "calibration_profile": str(profile_path),
                    "calibration_status": profile.status,
                },
            )
        else:
            verify_artifact(self.root, output_path, require_manifest=True)
        return CalibratedOddsCatalog(
            output_path.name,
            raw_catalog_id,
            OddsProjectionStore(self.root).latest(output_path.name),
            profile,
        )

    def _outcome_paths(self, season_id: str, methodology: str, model_signature: str) -> list[Path]:
        directory = self._outcomes_dir(season_id, methodology, model_signature)
        paths = sorted(directory.glob("*.jsonl")) if directory.exists() else []
        paths = [path for path in complete_artifact_paths(paths) if path.with_suffix(".manifest.json").exists()]
        for path in paths:
            verify_artifact(self.root, path, require_manifest=True)
        return paths

    def _load_outcomes(self, path: Path) -> list[LiveCalibrationOutcome]:
        return [LiveCalibrationOutcome(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]

    def _outcomes_dir(self, season_id: str, methodology: str, model_signature: str) -> Path:
        return self.root / "calibration" / "live" / "outcomes" / season_id / _signature(f"{methodology}:{model_signature}")

    def _profiles_dir(self, season_id: str, methodology: str, model_signature: str) -> Path:
        return self.root / "calibration" / "live" / "profiles" / season_id / _signature(f"{methodology}:{model_signature}")

    def _raw_catalog(self, applied_path: Path) -> tuple[Path, str]:
        verify_artifact(self.root, applied_path, require_manifest=True)
        manifest_path = applied_path.with_suffix(".manifest.json")
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        if document.get("artifact_type") != "calibrated_odds_projections":
            return applied_path, applied_path.name
        sources = {source.get("role"): resolve_artifact_ref(self.root, source["path"]) for source in document.get("sources", [])}
        raw_path = sources.get("raw_projection_catalog")
        if raw_path is None or raw_path.name == applied_path.name:
            raise ValueError(f"Calibrated projection catalog has no raw source: {applied_path}")
        verify_artifact(self.root, raw_path, require_manifest=True)
        return raw_path, raw_path.name

    @staticmethod
    def _is_calibrated_catalog(path: Path) -> bool:
        document = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        return document.get("artifact_type") == "calibrated_odds_projections"

    def _profile_for_calibrated_catalog(self, catalog_path: Path) -> LiveCalibrationProfile:
        document = json.loads(catalog_path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        sources = {source.get("role"): resolve_artifact_ref(self.root, source["path"]) for source in document.get("sources", [])}
        profile_path = sources.get("calibration_profile")
        if profile_path is None:
            raise ValueError(f"Calibrated projection catalog has no calibration profile: {catalog_path}")
        verify_artifact(self.root, profile_path, require_manifest=True)
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["created_at"] = datetime.fromisoformat(profile["created_at"])
        return LiveCalibrationProfile(**profile)

    @staticmethod
    def _model_signature(catalog_path: Path) -> str:
        manifest = json.loads(catalog_path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        parameters = manifest.get("parameters", {})
        dynamic_parameters = {
            "start_gameweek",
            "end_gameweek",
            "gameweeks_elapsed",
            "new_signing_count",
            "evidence_cutoff",
            "odds_coverage_by_gameweek",
            "odds_coverage_status",
        }
        model = {
            "methodology": manifest.get("methodology"),
            "parameters": {key: value for key, value in parameters.items() if key not in dynamic_parameters},
        }
        return _signature(json.dumps(model, sort_keys=True, separators=(",", ":")))

    @staticmethod
    def _actual_points(event_path: Path) -> dict[int, float]:
        document = json.loads(event_path.read_text(encoding="utf-8"))
        return {
            int(element["id"]): float((element.get("stats") or {}).get("total_points", 0))
            for element in document.get("payload", {}).get("elements", [])
            if isinstance(element, dict) and element.get("id") is not None
        }

    @staticmethod
    def _require_final_gameweek(bootstrap_path: Path, gameweek: int) -> None:
        document = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        finalized = any(
            isinstance(event, dict)
            and event.get("id") == gameweek
            and event.get("finished") is True
            and event.get("data_checked") is True
            for event in document.get("payload", {}).get("events", [])
        )
        if not finalized:
            raise ValueError(f"GW{gameweek} is not marked final by FPL")

    @staticmethod
    def _require_predeadline_inputs(
        raw_catalog_path: Path,
        applied_catalog_path: Path,
        decision_path: Path,
        decision_created_at: datetime,
        bootstrap_path: Path,
        gameweek: int,
    ) -> None:
        document = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        deadline_text = next(
            (
                event["deadline_time"]
                for event in document.get("payload", {}).get("events", [])
                if isinstance(event, dict) and event.get("id") == gameweek and isinstance(event.get("deadline_time"), str)
            ),
            None,
        )
        if deadline_text is None:
            raise ValueError(f"GW{gameweek} has no official deadline")
        deadline = datetime.fromisoformat(deadline_text.replace("Z", "+00:00"))
        if decision_created_at.tzinfo is None or decision_created_at >= deadline:
            raise ValueError(f"GW{gameweek} decision was not committed before its deadline")
        if not decision_path.exists():
            raise FileNotFoundError(f"Committed decision does not exist: {decision_path}")
        for catalog_path in {raw_catalog_path, applied_catalog_path}:
            manifest = json.loads(catalog_path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
            if created_at >= deadline:
                raise ValueError(f"GW{gameweek} projection catalog was created after its deadline")

    @staticmethod
    def _has_full_odds_coverage(catalog_path: Path, gameweek: int) -> bool:
        manifest_path = catalog_path.with_suffix(".manifest.json")
        try:
            parameters = json.loads(manifest_path.read_text(encoding="utf-8")).get("parameters", {})
            coverage = parameters.get("odds_coverage_by_gameweek", {})
            return float(coverage.get(str(gameweek), coverage.get(gameweek, 0))) >= 1.0
        except (OSError, ValueError, TypeError):
            return False


def calibrated_odds_rows(
    root: Path,
    catalog_id: str | None = None,
    season_id: str | None = None,
) -> tuple[list[OddsAdjustedGameweekProjection], LiveCalibrationProfile | None]:
    season_id = season_id or current_season_id(root)
    if season_id is None:
        return OddsProjectionStore(root).latest(catalog_id), None
    catalog = calibrated_odds_catalog(root, catalog_id, season_id)
    return catalog.rows, catalog.profile


def calibrated_odds_catalog(
    root: Path,
    catalog_id: str | None = None,
    season_id: str | None = None,
) -> CalibratedOddsCatalog:
    if catalog_id is None:
        catalog_id = OddsProjectionStore(root).latest_path().name
    season_id = season_id or current_season_id(root)
    if season_id is None:
        rows = OddsProjectionStore(root).latest(catalog_id)
        return CalibratedOddsCatalog(catalog_id, catalog_id, rows, None)
    return LiveCalibrationStore(root).materialize_catalog(catalog_id, season_id)


def current_season_id(root: Path) -> str | None:
    try:
        path, _ = SnapshotStore(root).latest_bootstrap()
    except FileNotFoundError:
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    deadlines = [
        datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
        for event in document.get("payload", {}).get("events", [])
        if isinstance(event, dict) and isinstance(event.get("deadline_time"), str)
    ]
    if not deadlines:
        return None
    now = datetime.now(timezone.utc)
    deadline = min((item for item in deadlines if item > now), default=max(deadlines))
    start_year = deadline.year if deadline.month >= 7 else deadline.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _weighted_affine_fit(rows: list[tuple[LiveCalibrationOutcome, float]]) -> tuple[float, float]:
    if not rows:
        return 1.0, 0.0
    total_weight = sum(weight for _, weight in rows)
    mean_x = sum(row.predicted_points / row.fixture_count * weight for row, weight in rows) / total_weight
    mean_y = sum(row.actual_points / row.fixture_count * weight for row, weight in rows) / total_weight
    denominator = sum(weight * (row.predicted_points / row.fixture_count - mean_x) ** 2 for row, weight in rows)
    if denominator == 0:
        return 1.0, mean_y - mean_x
    slope = sum(
        weight
        * (row.predicted_points / row.fixture_count - mean_x)
        * (row.actual_points / row.fixture_count - mean_y)
        for row, weight in rows
    ) / denominator
    return slope, mean_y - slope * mean_x


def _maturity(gameweeks: list[int], observations: int, settings: LiveCalibrationSettings) -> float:
    gameweek_maturity = min(1.0, (len(gameweeks) - settings.min_gameweeks + 1) / 5)
    observation_maturity = min(1.0, observations / max(1, settings.min_observations * 3))
    return gameweek_maturity * observation_maturity


def _signature(methodology: str) -> str:
    return sha256(methodology.encode("utf-8")).hexdigest()[:16]


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))
