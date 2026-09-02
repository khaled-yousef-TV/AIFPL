from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import Any, Literal, Mapping

import httpx
from pydantic import BaseModel, Field

from aifpl.artifacts import complete_artifact_paths, json_bytes, verify_artifact, write_immutable, write_manifest
from aifpl.config import ChipSettings, chip_settings, http_retry_settings
from aifpl.current_projections import CurrentPlayerProjection
from aifpl.fixtures import CurrentFixture
from aifpl.odds_projections import OddsAdjustedGameweekProjection
from aifpl.retry import retry_sync
from aifpl.security import redact_secrets

CHIP_NAMES = ("wildcard", "free_hit", "bench_boost", "triple_captain")


class ChipSlot(BaseModel):
    chip: str
    set: Literal[1, 2]
    used: bool = False
    used_gw: int | None = None


class ChipState(BaseModel):
    season_id: str
    slots: list[ChipSlot]
    updated_at: datetime
    active_chip: str | None = None
    active_chip_set: Literal[1, 2] | None = None
    active_gameweek: int | None = None


class ChipTimingSuggestion(BaseModel):
    chip: str
    gameweek: int
    rationale: str
    source: str
    source_url: str | None = None
    published_at: datetime | None = None


class ChipIntel(BaseModel):
    fetched_at: datetime
    stale: bool = False
    rules: dict[str, object] = Field(default_factory=dict)
    expected_dgw_gws: list[int] = Field(default_factory=list)
    expected_bgw_gws: list[int] = Field(default_factory=list)
    timing: list[ChipTimingSuggestion] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class ChipRecommendation(BaseModel):
    chip: str
    set: Literal[1, 2]
    status: Literal["recommend", "save", "used", "expired", "unavailable"]
    gameweek: int | None = None
    rationale: str
    confidence: float = 0.5
    conditions: dict[str, object] = Field(default_factory=dict)


class ChipAdvice(BaseModel):
    season_id: str
    gameweek: int
    recommendations: list[ChipRecommendation]
    schedule: dict[int, dict[str, object]]
    intel: ChipIntel
    created_at: datetime
    output_path: str = ""


class ChipStateError(ValueError):
    pass


class ChipIntelError(RuntimeError):
    pass


class ChipStateStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def latest(self, season_id: str) -> ChipState:
        directory = self.root / "chips" / "state" / season_id
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        files = [path for path in files if not path.name.endswith(".manifest.json")]
        if not files:
            state = ChipState(
                season_id=season_id,
                slots=[ChipSlot(chip=chip, set=chip_set) for chip in CHIP_NAMES for chip_set in (1, 2)],
                updated_at=datetime.now(timezone.utc),
            )
            return state
        verify_artifact(self.root, files[-1])
        state = ChipState.model_validate_json(files[-1].read_text(encoding="utf-8"))
        if state.season_id != season_id:
            raise ChipStateError("Chip state belongs to a different season")
        _validate_chip_state(state)
        return state

    def mark_used(self, season_id: str, chip: str, chip_set: int, gameweek: int) -> ChipState:
        lock_path = self.root / "chips" / "state" / season_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            flock(descriptor, LOCK_EX)
            current = self.validate_activation(season_id, chip, chip_set, gameweek)
            updated = current.model_copy(update={
                "slots": [
                    slot.model_copy(update={"used": True, "used_gw": gameweek})
                    if slot.chip == chip and slot.set == chip_set else slot
                    for slot in current.slots
                ],
                "active_chip": chip,
                "active_chip_set": chip_set,
                "active_gameweek": gameweek,
                "updated_at": datetime.now(timezone.utc),
            })
            now = updated.updated_at
            stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
            path = self.root / "chips" / "state" / season_id / f"{stamp}.json"
            write_immutable(path, json_bytes(updated.model_dump(mode="json"), pretty=True))
            write_manifest(
                self.root,
                path,
                artifact_type="chip_state",
                created_at=now.isoformat(),
                record_count=len(updated.slots),
                sources={},
                parameters={"chip": chip, "set": chip_set, "gameweek": gameweek},
            )
            return updated
        finally:
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    def validate_activation(self, season_id: str, chip: str, chip_set: int, gameweek: int) -> ChipState:
        if chip not in CHIP_NAMES or chip_set not in (1, 2):
            raise ValueError("chip must be one of wildcard/free_hit/bench_boost/triple_captain with set 1 or 2")
        if gameweek < 1 or gameweek > 38:
            raise ValueError("gameweek must be within 1..38")
        if chip == "free_hit" and gameweek == 1:
            raise ChipStateError("Free Hit is unavailable in GW1 under the 2026/27 rules")
        current = self.latest(season_id)
        selected = next((slot for slot in current.slots if slot.chip == chip and slot.set == chip_set), None)
        if selected is None:
            raise ChipStateError("Chip state does not contain the requested slot")
        if selected.used:
            raise ChipStateError(f"{chip} set {chip_set} was already used in GW{selected.used_gw}")
        settings = chip_settings()
        if chip_set == 1 and gameweek > settings.set1_end_gw:
            raise ChipStateError(f"Set-1 chips expired after GW{settings.set1_end_gw}")
        if chip_set == 2 and gameweek <= settings.set1_end_gw:
            raise ChipStateError(f"Set-2 chips are unavailable through GW{settings.set1_end_gw}")
        if any(slot.used and slot.used_gw == gameweek for slot in current.slots):
            raise ChipStateError(f"Only one chip may be activated in GW{gameweek}")
        if chip == "free_hit" and any(
            slot.chip == "free_hit" and slot.used and slot.used_gw == gameweek - 1
            for slot in current.slots
        ):
            raise ChipStateError("Free Hit cannot be activated in consecutive gameweeks")
        return current


def _validate_chip_state(state: ChipState) -> None:
    keys = [(slot.chip, slot.set) for slot in state.slots]
    if len(keys) != len(set(keys)):
        raise ChipStateError("Chip state contains duplicate chip slots")
    if any(slot.chip not in CHIP_NAMES for slot in state.slots):
        raise ChipStateError("Chip state contains an unsupported chip")
    if any(slot.used and slot.used_gw is None for slot in state.slots):
        raise ChipStateError("Used chip slots must record their gameweek")
    used_gameweeks = [slot.used_gw for slot in state.slots if slot.used]
    if len(used_gameweeks) != len(set(used_gameweeks)):
        raise ChipStateError("Only one chip may be activated in a gameweek")
    if any(
        slot.chip == "free_hit" and slot.used and any(
            other.chip == "free_hit" and other.used and other.used_gw == slot.used_gw - 1
            for other in state.slots if other is not slot
        )
        for slot in state.slots
    ):
        raise ChipStateError("Chip state contains consecutive Free Hit activations")


class ChipIntelFetcher:
    def __init__(self, root: Path, settings: ChipSettings | None = None, client: httpx.Client | None = None) -> None:
        self.root = root
        self.settings = settings or chip_settings()
        self.client = client or httpx.Client(timeout=20, headers={"User-Agent": "aifpl-chip-intel/1.0"})

    def fetch(self, gameweek: int) -> ChipIntel:
        fresh = self._cached()
        if fresh is not None:
            return fresh
        try:
            intel = self._fetch_fresh(gameweek)
        except Exception as exc:
            try:
                last = self.latest()
            except FileNotFoundError:
                raise ChipIntelError(redact_secrets(f"chip intel fetch failed: {type(exc).__name__}")) from exc
            return last.model_copy(update={"stale": True})
        now = intel.fetched_at
        path = self.root / "chips" / "intel" / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        write_immutable(path, json_bytes(intel.model_dump(mode="json"), pretty=True))
        write_manifest(
            self.root,
            path,
            artifact_type="chip_intel",
            created_at=now.isoformat(),
            record_count=len(intel.timing),
            sources={f"source_{index}": Path(source) if source.startswith("/") else Path("/dev/null") for index, source in enumerate(intel.sources)},
            parameters={"rules": intel.rules, "expected_dgw_gws": intel.expected_dgw_gws, "expected_bgw_gws": intel.expected_bgw_gws},
        )
        return intel

    def latest(self) -> ChipIntel:
        directory = self.root / "chips" / "intel"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        files = [path for path in files if not path.name.endswith(".manifest.json")]
        if not files:
            raise FileNotFoundError("No chip intel exists")
        return ChipIntel.model_validate_json(files[-1].read_text(encoding="utf-8"))

    def _cached(self) -> ChipIntel | None:
        try:
            latest = self.latest()
        except FileNotFoundError:
            return None
        if datetime.now(timezone.utc) - latest.fetched_at <= timedelta(hours=self.settings.intel_cache_hours):
            return latest
        return None

    def _fetch_fresh(self, gameweek: int) -> ChipIntel:
        rules, rule_sources = self._fetch_rules()
        timing: list[ChipTimingSuggestion] = []
        expected_dgw: set[int] = set()
        expected_bgw: set[int] = set()
        sources: list[str] = list(rule_sources)
        for chip in CHIP_NAMES:
            try:
                reddit = self._fetch_reddit(chip, gameweek)
            except Exception:
                continue
            if reddit is None:
                continue
            text, url = reddit
            sources.append(url)
            for found in _extract_gws(text):
                timing.append(ChipTimingSuggestion(
                    chip=chip, gameweek=found,
                    rationale=_snippet(text),
                    source="reddit_r_fantasypl",
                    source_url=url,
                    published_at=datetime.now(timezone.utc),
                ))
            if "double gameweek" in text.casefold():
                expected_dgw.update(_extract_gws(text))
            if "blank gameweek" in text.casefold():
                expected_bgw.update(_extract_gws(text))
        return ChipIntel(
            fetched_at=datetime.now(timezone.utc),
            rules=rules,
            expected_dgw_gws=sorted(expected_dgw),
            expected_bgw_gws=sorted(expected_bgw),
            timing=timing[: self.settings.intel_max_timing],
            sources=sources,
        )

    def _fetch_rules(self) -> tuple[dict[str, object], list[str]]:
        text = self._fetch_text(self.settings.rules_url)
        chips_per_set = 2 if "eight chips" in text.casefold() else 1
        set1_end = self.settings.set1_end_gw
        match = re.search(r"Gameweek\s+(\d{1,2})\s+deadline", text)
        if match:
            set1_end = int(match.group(1))
        return {"chips_per_set": chips_per_set, "set1_end_gw": set1_end}, [self.settings.rules_url]

    def _fetch_reddit(self, chip: str, gameweek: int) -> tuple[str, str] | None:
        query = f"{chip} double gameweek OR blank gameweek"
        url = "https://old.reddit.com/r/FantasyPL/search.json"
        try:
            response = retry_sync(
                lambda: self.client.get(
                    url,
                    params={"q": query, "restrict_sr": "on", "sort": "new", "limit": self.settings.reddit_limit},
                ),
                http_retry_settings(),
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ChipIntelError(redact_secrets(f"reddit fetch failed: {type(exc).__name__}")) from exc
        text_parts = []
        for child in payload.get("data", {}).get("children", []):
            data = child.get("data", {})
            text_parts.append(f"{data.get('title', '')} {data.get('selftext', '')}")
        combined = " ".join(text_parts)
        if not combined.strip():
            return None
        return combined, url

    def _fetch_text(self, url: str) -> str:
        try:
            response = retry_sync(lambda: self.client.get(url), http_retry_settings())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ChipIntelError(redact_secrets(f"rules fetch failed: {type(exc).__name__}")) from exc
        return response.text


def detect_schedule(
    fixtures: list[CurrentFixture] | list[Mapping[str, Any]],
    expected_dgw: list[int] | None = None,
    expected_bgw: list[int] | None = None,
) -> dict[int, dict[str, object]]:
    """Confirmed double/blank gameweeks from fixtures, merged with expected windows."""
    expected_dgw = expected_dgw or []
    expected_bgw = expected_bgw or []
    fixtures_by_gw: dict[int, list[object]] = {}
    for fixture in fixtures:
        gameweek = _field(fixture, "gameweek", _field(fixture, "event"))
        if gameweek is not None:
            fixtures_by_gw.setdefault(int(gameweek), []).append(fixture)
    schedule: dict[int, dict[str, object]] = {}
    for gameweek, rows in fixtures_by_gw.items():
        team_counts: dict[int, int] = {}
        for fixture in rows:
            home = _field(fixture, "home_team_id", _field(fixture, "team_h"))
            away = _field(fixture, "away_team_id", _field(fixture, "team_a"))
            if home is not None:
                team_counts[int(home)] = team_counts.get(int(home), 0) + 1
            if away is not None:
                team_counts[int(away)] = team_counts.get(int(away), 0) + 1
        double = any(count >= 2 for count in team_counts.values())
        explicit_blank = any(_explicit_blank(fixture) for fixture in rows)
        blank = explicit_blank or len(rows) < 10
        schedule[gameweek] = {
            "fixtures": len(rows),
            "double": double,
            "blank": blank,
            "teams_with_two_fixtures": sum(1 for count in team_counts.values() if count >= 2),
        }
        if explicit_blank:
            schedule[gameweek]["explicit_blank"] = True
    for gameweek in expected_dgw:
        entry = schedule.setdefault(gameweek, {"fixtures": 0, "double": False, "blank": False, "teams_with_two_fixtures": 0})
        entry["double"] = True
        entry["expected"] = True
    for gameweek in expected_bgw:
        entry = schedule.setdefault(gameweek, {"fixtures": 0, "double": False, "blank": False, "teams_with_two_fixtures": 0})
        entry["blank"] = True
        entry["expected"] = True
    return schedule


def _merge_projection_schedule(
    schedule: dict[int, dict[str, object]],
    projections: list[OddsAdjustedGameweekProjection],
) -> None:
    """Use production projection rows to identify blank GWs without row absence."""
    by_gameweek: dict[int, list[object]] = {}
    for row in projections:
        gameweek = _field(row, "gameweek")
        if gameweek is not None:
            by_gameweek.setdefault(int(gameweek), []).append(row)
    for gameweek, rows in by_gameweek.items():
        explicit_blank = any(_explicit_blank(row) for row in rows)
        fixture_counts = [_fixture_count(row) for row in rows]
        had_fixture_schedule = gameweek in schedule and bool(schedule[gameweek].get("fixtures"))
        entry = schedule.setdefault(gameweek, _empty_schedule_entry())
        if explicit_blank:
            entry["blank"] = True
            entry["explicit_blank"] = True
        elif not had_fixture_schedule:
            # A production catalog contains a row for every player and GW;
            # only an explicit zero fixture count means this GW is blank.
            known_counts = [count for count in fixture_counts if count is not None]
            if known_counts and all(count <= 0 for count in known_counts):
                entry["blank"] = True


def _player_has_blank_fixture(row: object | None, schedule_entry: Mapping[str, object] | None) -> bool:
    if row is not None:
        if _explicit_blank(row):
            return True
        count = _fixture_count(row)
        if count is not None:
            return count <= 0
    # Missing projection rows are deliberately not treated as blanks.  An
    # explicit schedule marker is still authoritative when the catalog carries
    # a deliberately sparse player population.
    return bool(schedule_entry and schedule_entry.get("explicit_blank") and row is not None)


def _fixture_count(row: object) -> int | None:
    value = _field(row, "fixture_count")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _explicit_blank(row: object) -> bool:
    for name in ("blank", "is_blank", "is_blank_gameweek", "blank_gameweek", "explicit_blank", "blank_flag"):
        value = _field(row, name)
        if value is not None:
            if isinstance(value, str):
                return value.casefold() in {"1", "true", "yes"}
            return bool(value)
    return False


def _field(row: object, name: str, default: object | None = None) -> object | None:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _empty_schedule_entry() -> dict[str, object]:
    return {"fixtures": 0, "double": False, "blank": False, "teams_with_two_fixtures": 0}


class ChipAdvisor:
    def __init__(self, settings: ChipSettings | None = None) -> None:
        self.settings = settings or chip_settings()

    def evaluate(
        self,
        season_id: str,
        gameweek: int,
        state: ChipState,
        fixtures: list[CurrentFixture],
        projections: list[OddsAdjustedGameweekProjection],
        committed_player_ids: list[int],
        starting_xi_ids: list[int],
        best_squad_ids: list[int],
        intel: ChipIntel,
    ) -> ChipAdvice:
        schedule = detect_schedule(fixtures, intel.expected_dgw_gws, intel.expected_bgw_gws)
        _merge_projection_schedule(schedule, projections)
        by_player_gw: dict[tuple[int, int], OddsAdjustedGameweekProjection] = {
            (row.player_id, row.gameweek): row for row in projections
        }
        available_gws = sorted({row.gameweek for row in projections})
        recommendations: list[ChipRecommendation] = []
        for slot in state.slots:
            if slot.used:
                recommendations.append(ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="used", rationale=f"Used in GW{slot.used_gw}.",
                ))
                continue
            if slot.set == 1 and gameweek > int(intel.rules.get("set1_end_gw", self.settings.set1_end_gw)):
                recommendations.append(ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="expired",
                    rationale=f"Set-1 {slot.chip} expired at the GW{intel.rules.get('set1_end_gw', self.settings.set1_end_gw)} deadline.",
                ))
                continue
            if slot.set == 2 and gameweek <= int(intel.rules.get("set1_end_gw", self.settings.set1_end_gw)):
                recommendations.append(ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="unavailable",
                    rationale="Set-2 chips open after the GW19 deadline.",
                ))
                continue
            if slot.chip == "free_hit" and gameweek == 1:
                recommendations.append(ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="unavailable",
                    rationale="Free Hit is unavailable in GW1 under the 2026/27 rules.",
                ))
                continue
            recommendation = self._evaluate_slot(
                slot, gameweek, schedule, by_player_gw, available_gws,
                committed_player_ids, starting_xi_ids, best_squad_ids,
            )
            recommendations.append(recommendation)
        advice = ChipAdvice(
            season_id=season_id, gameweek=gameweek, recommendations=recommendations,
            schedule=schedule, intel=intel, created_at=datetime.now(timezone.utc),
        )
        return advice

    def _evaluate_slot(
        self,
        slot: ChipSlot,
        gameweek: int,
        schedule: dict[int, dict[str, object]],
        by_player_gw: dict[tuple[int, int], OddsAdjustedGameweekProjection],
        available_gws: list[int],
        committed_player_ids: list[int],
        starting_xi_ids: list[int],
        best_squad_ids: list[int],
    ) -> ChipRecommendation:
        set_deadline = self.settings.set1_end_gw if slot.set == 1 else 38
        remaining = set_deadline - gameweek
        pressure = max(0.0, min(1.0, (self.settings.use_window_gws - remaining) / max(1, self.settings.use_window_gws)))
        horizon = [gw for gw in available_gws if gw >= gameweek][:4]
        if slot.chip == "wildcard":
            committed = sum(
                by_player_gw[(player_id, gw)].projected_points
                for gw in horizon for player_id in committed_player_ids
                if (player_id, gw) in by_player_gw
            )
            best = sum(
                by_player_gw[(player_id, gw)].projected_points
                for gw in horizon for player_id in best_squad_ids
                if (player_id, gw) in by_player_gw
            )
            gap = best - committed
            threshold = self.settings.wildcard_points_gap - pressure * (
                self.settings.wildcard_points_gap - self.settings.wildcard_gap_floor
            )
            if gap >= threshold:
                expiry_note = " Set-1 wildcard otherwise expires at the GW19 deadline." if slot.set == 1 else ""
                return ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="recommend", gameweek=gameweek,
                    rationale=(
                        f"Best-available squad projects {gap:.1f} points better over {len(horizon)} GWs"
                        f"{' (use-it window: threshold relaxed to %.1f)' % threshold}.{expiry_note}"
                    ),
                    confidence=min(1.0, gap / max(1.0, threshold * 2)),
                    conditions={"projected_gap": round(gap, 4), "horizon_gws": horizon, "threshold": round(threshold, 4)},
                )
            return ChipRecommendation(
                chip=slot.chip, set=slot.set, status="save",
                rationale=f"Current squad trails best-available by only {gap:.1f} points over {len(horizon)} GWs.",
                confidence=0.5,
                conditions={"projected_gap": round(gap, 4), "horizon_gws": horizon},
            )
        next_dgw = next((gw for gw in sorted(schedule) if gw >= gameweek and schedule[gw].get("double")), None)
        if slot.chip == "bench_boost":
            if next_dgw is None:
                if pressure > 0.0:
                    bench_ids = [player_id for player_id in committed_player_ids if player_id not in set(starting_xi_ids)]
                    bench_points = sum(
                        by_player_gw[(player_id, gameweek)].projected_points
                        for player_id in bench_ids if (player_id, gameweek) in by_player_gw
                    )
                    floor = self.settings.bench_boost_floor_points
                    if bench_points >= floor:
                        return ChipRecommendation(
                            chip=slot.chip, set=slot.set, status="recommend", gameweek=gameweek,
                            rationale=(
                                f"No double gameweek before expiry; bench projects {bench_points:.1f} "
                                f"this GW (use-it floor {floor:.1f}) — otherwise the chip is forfeited "
                                "at the GW19 deadline."
                            ),
                            confidence=min(1.0, bench_points / (floor * 1.5)),
                            conditions={"bench_projected_points": round(bench_points, 4), "use_it_window": True},
                        )
                    return ChipRecommendation(
                        chip=slot.chip, set=slot.set, status="save", gameweek=gameweek,
                        rationale=f"Expiry window open but the bench only projects {bench_points:.1f} this GW.",
                        confidence=0.5,
                        conditions={"bench_projected_points": round(bench_points, 4)},
                    )
                return ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="save",
                    rationale="No double gameweek detected; Bench Boost is worth saving for one.",
                    confidence=0.4,
                )
            bench_ids = [player_id for player_id in committed_player_ids if player_id not in set(starting_xi_ids)]
            bench_points = sum(
                by_player_gw[(player_id, next_dgw)].projected_points
                for player_id in bench_ids if (player_id, next_dgw) in by_player_gw
            )
            if bench_points >= self.settings.bench_boost_bench_points:
                return ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="recommend", gameweek=next_dgw,
                    rationale=f"Double gameweek in GW{next_dgw} with bench projecting {bench_points:.1f} points.",
                    confidence=min(1.0, bench_points / (self.settings.bench_boost_bench_points * 1.5)),
                    conditions={"bench_projected_points": round(bench_points, 4), "double_gameweek": next_dgw},
                )
            return ChipRecommendation(
                chip=slot.chip, set=slot.set, status="save", gameweek=next_dgw,
                rationale=f"Double gameweek in GW{next_dgw} but the bench only projects {bench_points:.1f} points.",
                confidence=0.5,
                conditions={"bench_projected_points": round(bench_points, 4), "double_gameweek": next_dgw},
            )
        if slot.chip == "triple_captain":
            if next_dgw is None:
                if pressure > 0.0:
                    captain_candidates = sorted(
                        (by_player_gw[(player_id, gameweek)].projected_points for player_id in starting_xi_ids if (player_id, gameweek) in by_player_gw),
                        reverse=True,
                    )
                    if len(captain_candidates) >= 2:
                        top, second = captain_candidates[0], captain_candidates[1]
                        floor = self.settings.tc_captain_floor_points
                        margin_floor = self.settings.tc_margin_floor
                        if top >= floor and (top - second) >= margin_floor:
                            return ChipRecommendation(
                                chip=slot.chip, set=slot.set, status="recommend", gameweek=gameweek,
                                rationale=(
                                    f"No double gameweek before expiry; top starter projects {top:.1f} "
                                    f"this GW (use-it floor {floor:.1f}) — otherwise the chip is forfeited "
                                    "at the GW19 deadline."
                                ),
                                confidence=min(1.0, (top - second) / max(1.0, margin_floor * 2)),
                                conditions={"captain_projected_points": round(top, 4), "use_it_window": True},
                            )
                    return ChipRecommendation(
                        chip=slot.chip, set=slot.set, status="save", gameweek=gameweek,
                        rationale="Expiry window open but no starter clears the use-it captain floor.",
                        confidence=0.5,
                    )
                return ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="save",
                    rationale="No double gameweek detected; Triple Captain is worth saving for one.",
                    confidence=0.4,
                )
            captain_candidates = sorted(
                (by_player_gw[(player_id, next_dgw)].projected_points for player_id in starting_xi_ids if (player_id, next_dgw) in by_player_gw),
                reverse=True,
            )
            if len(captain_candidates) < 2:
                return ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="save", gameweek=next_dgw,
                    rationale="Not enough starter projections for a Triple Captain assessment.",
                    confidence=0.3,
                )
            top, second = captain_candidates[0], captain_candidates[1]
            if top >= self.settings.tc_captain_points and (top - second) >= self.settings.tc_margin:
                return ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="recommend", gameweek=next_dgw,
                    rationale=f"Top starter projects {top:.1f} in the GW{next_dgw} double with a {top - second:.1f} point margin.",
                    confidence=min(1.0, (top - second) / (self.settings.tc_margin * 2)),
                    conditions={"captain_projected_points": round(top, 4), "margin": round(top - second, 4), "double_gameweek": next_dgw},
                )
            return ChipRecommendation(
                chip=slot.chip, set=slot.set, status="save", gameweek=next_dgw,
                rationale=f"GW{next_dgw} double has no standout captain (top {top:.1f}, margin {top - second:.1f}).",
                confidence=0.5,
                conditions={"captain_projected_points": round(top, 4), "margin": round(top - second, 4), "double_gameweek": next_dgw},
            )
        if slot.chip == "free_hit":
            next_bgw = next((gw for gw in sorted(schedule) if gw >= gameweek and schedule[gw].get("blank")), None)
            if next_bgw is None:
                if pressure > 0.0:
                    return ChipRecommendation(
                        chip=slot.chip, set=slot.set, status="recommend", gameweek=gameweek,
                        rationale=(
                            "No blank gameweek before expiry; use the Free Hit this GW so it is not "
                            "forfeited at the GW19 deadline."
                        ),
                        confidence=0.6,
                        conditions={"use_it_window": True},
                    )
                return ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="save",
                    rationale="No blank gameweek detected; Free Hit is worth saving for one.",
                    confidence=0.4,
                )
            without_fixture = sum(
                1 for player_id in starting_xi_ids
                if _player_has_blank_fixture(by_player_gw.get((player_id, next_bgw)), schedule.get(next_bgw))
            )
            if without_fixture >= self.settings.fh_starters_without_fixture:
                return ChipRecommendation(
                    chip=slot.chip, set=slot.set, status="recommend", gameweek=next_bgw,
                    rationale=f"Blank gameweek in GW{next_bgw} leaves {without_fixture} starters without a fixture.",
                    confidence=min(1.0, without_fixture / (self.settings.fh_starters_without_fixture + 2)),
                    conditions={"starters_without_fixture": without_fixture, "blank_gameweek": next_bgw},
                )
            return ChipRecommendation(
                chip=slot.chip, set=slot.set, status="save", gameweek=next_bgw,
                rationale=f"Blank gameweek in GW{next_bgw} but only {without_fixture} starters are affected.",
                confidence=0.5,
                conditions={"starters_without_fixture": without_fixture, "blank_gameweek": next_bgw},
            )
        raise ValueError(f"Unknown chip: {slot.chip}")


class ChipAdviceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, advice: ChipAdvice) -> ChipAdvice:
        now = advice.created_at
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / "chips" / "advice" / f"{stamp}.json"
        advice = advice.model_copy(update={"output_path": str(path)})
        write_immutable(path, json_bytes(advice.model_dump(mode="json"), pretty=True))
        write_manifest(
            self.root,
            path,
            artifact_type="chip_advice",
            created_at=now.isoformat(),
            record_count=len(advice.recommendations),
            sources={},
            parameters={"season_id": advice.season_id, "gameweek": advice.gameweek},
        )
        return advice

    def latest(self) -> ChipAdvice:
        directory = self.root / "chips" / "advice"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        files = [path for path in files if not path.name.endswith(".manifest.json")]
        if not files:
            raise FileNotFoundError("No chip advice exists")
        return ChipAdvice.model_validate_json(files[-1].read_text(encoding="utf-8"))


def _extract_gws(text: str) -> list[int]:
    matches = re.findall(r"\b(?:GW|gameweek)\s*(\d{1,2})\b", text, flags=re.IGNORECASE)
    return sorted({int(value) for value in matches if 1 <= int(value) <= 38})


def _snippet(text: str, limit: int = 160) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:limit]
