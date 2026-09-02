from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, Field

from aifpl.artifacts import json_bytes, write_immutable
from aifpl.execution import ExecutionConfirmation, ExecutionConfirmationStore
from aifpl.fpl import FplClient
from aifpl.hermes import HermesDecision
from aifpl.live_calibration import LiveCalibrationStore
from aifpl.odds_projections import OddsProjectionStore
from aifpl.snapshots import SnapshotStore


class ScoredPlayer(BaseModel):
    element: int
    name: str
    position: str
    projected: float
    actual: float
    minutes: int | None = None
    played: bool | None = None
    role: str | None = None
    multiplier: int = 1


class ScoredAutoSub(BaseModel):
    out_element: int
    in_element: int
    out_name: str
    in_name: str
    out_actual: float = 0.0
    in_actual: float = 0.0


class ScoredTransfer(BaseModel):
    out_element: int
    in_element: int
    out_name: str
    in_name: str
    out_actual: float
    in_actual: float
    delta: float


class DecisionScore(BaseModel):
    decision_path: str
    gameweek: int
    season_id: str
    action: str = ""
    scoring_at: datetime
    projection_catalog: str
    event_snapshot: str
    xi_projected: float
    xi_actual: float
    bench_projected: float
    bench_actual: float
    captain: ScoredPlayer | None = None
    transfers: list[ScoredTransfer] = Field(default_factory=list)
    players: list[ScoredPlayer] = Field(default_factory=list)
    total_projected: float
    total_actual: float
    output_path: str = ""
    starting_xi_ids: list[int] = Field(default_factory=list)
    bench_ids: list[int] = Field(default_factory=list)
    effective_xi_ids: list[int] = Field(default_factory=list)
    autosubs: list[ScoredAutoSub] = Field(default_factory=list)
    vice_captain: ScoredPlayer | None = None
    vice_captain_id: int | None = None
    effective_captain_id: int | None = None
    captain_multiplier: int = 1
    vice_captain_promoted: bool = False
    captain_bonus: float = 0.0
    starting_xi_actual: float = 0.0
    autosub_actual: float = 0.0
    bench_boost_actual: float = 0.0
    official_xi_actual: float = 0.0
    transfer_hit: int = 0
    hit_cost: int = 0
    free_transfers_before: int | None = None
    transfers_made: int = 0
    chip: str | None = None
    chip_set: int | None = None
    active_chip: str | None = None
    active_chip_set: int | None = None
    chip_state: dict[str, object] = Field(default_factory=dict)
    evaluation_basis: str = "recommendation_only"
    execution_confirmation_path: str | None = None


@dataclass(frozen=True)
class _ActualPlayer:
    points: float
    minutes: int | None


class DecisionScorer:
    """Score a committed Hermes decision against the completed gameweek's
    actuals. Predictions come from the odds projection catalog used at
    decision time; actuals come from the official event-live snapshot."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def score(
        self,
        decision_path: Path | str,
        event: int | None = None,
        chip_state: object | None = None,
    ) -> DecisionScore:
        decision_path = Path(decision_path)
        decision_document = json.loads(decision_path.read_text(encoding="utf-8"))
        decision = HermesDecision.model_validate(decision_document)
        if event is not None and event != decision.gameweek:
            raise ValueError(
                f"Decision is for GW{decision.gameweek}; cannot score it as GW{event}"
            )
        gameweek = event or decision.gameweek
        execution = ExecutionConfirmationStore(self.root).latest_for_decision(
            decision_path, decision.season_id, gameweek,
        )
        existing = self._matching_score(decision_path, gameweek, execution)
        if existing is not None:
            return existing
        if execution is not None:
            decision_document = dict(decision_document)
            decision_document.update({
                "bench_ids": execution.bench_ids,
                "vice_captain_id": execution.vice_captain_id,
                "active_chip": execution.active_chip,
                "active_chip_set": execution.active_chip_set,
                "transfers_out": execution.transfers_out,
                "transfers_in": execution.transfers_in,
                "hit_cost": execution.hit_cost,
                "free_transfers_before": execution.free_transfers_before,
            })
            decision = decision.model_copy(update={
                "squad": decision.squad.model_copy(update={"player_ids": execution.squad_ids}),
                "starting_xi_ids": execution.starting_xi_ids,
                "captain_id": execution.captain_id,
                "vice_captain_id": execution.vice_captain_id,
                "transfers_out": execution.transfers_out,
                "transfers_in": execution.transfers_in,
                "active_chip": execution.active_chip,
                "active_chip_set": execution.active_chip_set,
            })
        catalog_path = self._catalog_for_gameweek(
            gameweek,
            decision.horizon_plan.projection_catalog if decision.horizon_plan is not None else None,
        )
        rows = [row for row in OddsProjectionStore(self.root).latest(catalog_path.name) if row.gameweek == gameweek]
        projections = {row.player_id: row.projected_points for row in rows}
        names = {row.player_id: (row.player_name, row.position) for row in rows}
        actual_details = self._event_player_stats(gameweek)
        actuals = {element: detail.points for element, detail in actual_details.items()}

        xi_ids = _unique_ids(decision.starting_xi_ids)
        if decision.action != "adopt_initial" and len(decision.transfers_out) != len(decision.transfers_in):
            raise ValueError("Decision transfer lists must contain the same number of players")
        bench_ids = _ordered_bench_ids(decision_document, decision.squad.player_ids, xi_ids)
        vice_captain_id = _vice_captain_id(decision, decision_document, gameweek)
        _validate_decision_lineup(decision, xi_ids, vice_captain_id)
        effective_xi_ids, autosubs = _reconstruct_effective_xi(
            xi_ids, bench_ids, actual_details, names,
        )
        captain_projected = projections.get(decision.captain_id, 0.0)
        starting_xi_actual = sum(actuals.get(element, 0.0) for element in xi_ids)
        effective_xi_actual = sum(actuals.get(element, 0.0) for element in effective_xi_ids)
        captain_played = _played(actual_details.get(decision.captain_id))
        vice_played = (
            vice_captain_id is not None
            and vice_captain_id in xi_ids
            and _played(actual_details.get(vice_captain_id))
        )
        vice_captain_promoted = not captain_played and vice_played
        multiplier_id = decision.captain_id if captain_played else vice_captain_id if vice_captain_promoted else None
        bench_projected = sum(projections.get(element, 0.0) for element in bench_ids)
        bench_actual = sum(actuals.get(element, 0.0) for element in bench_ids)
        chip, chip_set, persisted_chip_state = self._chip_for_gameweek(
            decision, decision_document, gameweek, chip_state, execution,
        )
        chip_is_triple_captain = chip == "triple_captain"
        captain_multiplier = (
            3 if chip_is_triple_captain and captain_played
            else 2 if captain_played
            else 1
        )
        effective_captain_multiplier = (
            captain_multiplier
            if multiplier_id == decision.captain_id
            else 2 if multiplier_id == vice_captain_id
            else 1
        )
        captain_bonus = (
            max(0, effective_captain_multiplier - 1) * actuals.get(multiplier_id, 0.0)
            if multiplier_id is not None else 0.0
        )
        projected_captain_bonus = 2 if chip_is_triple_captain else 1
        xi_projected = (
            sum(projections.get(element, 0.0) for element in xi_ids)
            + projected_captain_bonus * captain_projected
        )
        bench_boost_actual = bench_actual if chip == "bench_boost" else 0.0
        # Bench Boost counts the named XI and every bench player.  Applying an
        # autosub first would count a bench player twice, so only ordinary
        # gameweeks use the effective substituted XI.
        scored_xi_actual = starting_xi_actual if chip == "bench_boost" else effective_xi_actual
        autosub_actual = 0.0 if chip == "bench_boost" else effective_xi_actual - starting_xi_actual
        xi_actual = scored_xi_actual + captain_bonus
        transfer_hit, free_transfers_before = _transfer_hit(
            decision, decision_document, gameweek, chip,
        )
        projected_bench_bonus = bench_projected if chip == "bench_boost" else 0.0

        players = [
            ScoredPlayer(
                element=element, name=names.get(element, (f"#{element}", "?"))[0],
                position=names.get(element, ("?", "?"))[1],
                projected=round(projections.get(element, 0.0), 4), actual=actuals.get(element, 0.0),
                minutes=actual_details.get(element).minutes if element in actual_details else None,
                played=_played(actual_details.get(element)) if element in actual_details else False,
                role=(
                    "captain" if element == decision.captain_id
                    else "vice_captain" if element == vice_captain_id
                    else "starter" if element in xi_ids
                    else "bench"
                ),
                multiplier=(
                    effective_captain_multiplier if element == decision.captain_id and multiplier_id == element
                    else 2 if element == multiplier_id
                    else 1
                ),
            )
            for element in decision.squad.player_ids
        ]
        transfers = [
            ScoredTransfer(
                out_element=out_element, in_element=in_element,
                out_name=names.get(out_element, (f"#{out_element}", "?"))[0],
                in_name=names.get(in_element, (f"#{in_element}", "?"))[0],
                out_actual=actuals.get(out_element, 0.0), in_actual=actuals.get(in_element, 0.0),
                delta=round(actuals.get(in_element, 0.0) - actuals.get(out_element, 0.0), 4),
            )
            for out_element, in_element in _pair_transfer_ids(
                decision.transfers_out, decision.transfers_in, names,
            )
        ]
        captain = _scored_player(
            decision.captain_id, projections, actual_details, names, role="captain",
            multiplier=effective_captain_multiplier if multiplier_id == decision.captain_id else 1,
        )
        vice_captain = (
            _scored_player(
                vice_captain_id, projections, actual_details, names, role="vice_captain",
                multiplier=2 if multiplier_id == vice_captain_id else 1,
            )
            if vice_captain_id is not None else None
        )
        record = DecisionScore(
            decision_path=str(decision_path), gameweek=gameweek, season_id=decision.season_id,
            action=decision.action, scoring_at=datetime.now(timezone.utc), projection_catalog=str(catalog_path),
            event_snapshot=str(self._event_snapshot_path(gameweek)),
            xi_projected=round(xi_projected, 4), xi_actual=round(xi_actual, 4),
            bench_projected=round(bench_projected, 4), bench_actual=round(bench_actual, 4),
            captain=captain, vice_captain=vice_captain,
            transfers=transfers, players=players,
            total_projected=round(xi_projected + projected_bench_bonus - transfer_hit, 4),
            total_actual=round(xi_actual + bench_boost_actual - transfer_hit, 4),
            starting_xi_ids=xi_ids, bench_ids=bench_ids, effective_xi_ids=effective_xi_ids,
            autosubs=autosubs, vice_captain_id=vice_captain_id,
            effective_captain_id=multiplier_id, captain_multiplier=captain_multiplier,
            vice_captain_promoted=vice_captain_promoted, captain_bonus=round(captain_bonus, 4),
            starting_xi_actual=round(starting_xi_actual, 4), autosub_actual=round(autosub_actual, 4),
            bench_boost_actual=round(bench_boost_actual, 4), official_xi_actual=round(xi_actual, 4),
            transfer_hit=transfer_hit, hit_cost=transfer_hit,
            free_transfers_before=free_transfers_before,
            transfers_made=0 if decision.action == "adopt_initial" else len(decision.transfers_in),
            chip=chip, chip_set=chip_set, active_chip=chip, active_chip_set=chip_set,
            chip_state=persisted_chip_state,
            evaluation_basis="confirmed_execution" if execution is not None else "recommendation_only",
            execution_confirmation_path=execution.output_path if execution is not None else None,
        )
        lock_path = self.root / "scoring" / "decisions" / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            flock(descriptor, LOCK_EX)
            existing = self._matching_score(decision_path, gameweek, execution)
            if existing is not None:
                return existing
            output_path = self.root / "scoring" / "decisions" / f"{record.scoring_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
            record = record.model_copy(update={"output_path": str(output_path)})
            write_immutable(output_path, json_bytes(record.model_dump(mode="json"), pretty=True))
            return record
        finally:
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    def latest(self) -> DecisionScore:
        directory = self.root / "scoring" / "decisions"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        if not files:
            raise FileNotFoundError("No scored decisions exist; run score-decisions first")
        return DecisionScore.model_validate_json(files[-1].read_text(encoding="utf-8"))

    def recent(self, limit: int = 5) -> list[DecisionScore]:
        directory = self.root / "scoring" / "decisions"
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        return [DecisionScore.model_validate_json(path.read_text(encoding="utf-8")) for path in reversed(files[-limit:])]

    def is_scored(self, decision_path: Path | str, event: int) -> bool:
        path = Path(decision_path)
        try:
            decision = HermesDecision.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        execution = ExecutionConfirmationStore(self.root).latest_for_decision(
            path, decision.season_id, event,
        )
        return self._matching_score(path, event, execution) is not None

    def _matching_score(
        self,
        decision_path: Path | str,
        event: int,
        execution: ExecutionConfirmation | None,
    ) -> DecisionScore | None:
        directory = self.root / "scoring" / "decisions"
        if not directory.exists():
            return None
        expected_execution_path = execution.output_path if execution is not None else None
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                record = DecisionScore.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                record.gameweek == event
                and record.decision_path == str(decision_path)
                and record.execution_confirmation_path == expected_execution_path
            ):
                return record
        return None

    def require_final_gameweek(self, gameweek: int) -> None:
        path, _ = SnapshotStore(self.root).latest_bootstrap()
        document = json.loads(path.read_text(encoding="utf-8"))
        payload = document.get("payload", document)
        events = payload.get("events", []) if isinstance(payload, dict) else []
        finalized = any(
            isinstance(event, dict)
            and event.get("id") == gameweek
            and event.get("finished") is True
            and event.get("data_checked") is True
            for event in events
        )
        if not finalized:
            raise ValueError(f"GW{gameweek} is not marked final by FPL")

    def _catalog_for_gameweek(self, gameweek: int, catalog_id: str | None = None) -> Path:
        directory = self.root / "normalized" / "current" / "odds_projections"
        if catalog_id:
            rows = OddsProjectionStore(self.root).latest(catalog_id)
            if gameweek in {row.gameweek for row in rows}:
                return directory / catalog_id
            raise ValueError(f"Pinned projection catalog does not contain GW{gameweek}: {catalog_id}")
        candidates: list[tuple[datetime, Path]] = []
        for path in directory.glob("*.jsonl"):
            manifest = path.with_suffix(".manifest.json")
            if manifest.exists():
                candidates.append((datetime.fromisoformat(json.loads(manifest.read_text(encoding="utf-8"))["created_at"]), path))
        for _, path in sorted(candidates, reverse=True):
            try:
                rows = OddsProjectionStore(self.root).latest(path.name)
            except ValueError:
                continue
            if gameweek in {row.gameweek for row in rows}:
                return path
        raise FileNotFoundError(f"No odds projection catalog contains gameweek {gameweek}")

    def _event_snapshot_path(self, gameweek: int) -> Path:
        path, _ = SnapshotStore(self.root).latest_event_live(gameweek)
        return path

    def _event_player_stats(self, gameweek: int) -> dict[int, _ActualPlayer]:
        path, _ = SnapshotStore(self.root).latest_event_live(gameweek)
        document = json.loads(path.read_text(encoding="utf-8"))
        payload = document.get("payload", document)
        elements = payload.get("elements", []) if isinstance(payload, dict) else []
        actuals: dict[int, _ActualPlayer] = {}
        for element in elements:
            if not isinstance(element, dict) or element.get("id") is None:
                continue
            stats = element.get("stats") if isinstance(element.get("stats"), dict) else {}
            points_value = stats.get("total_points", element.get("total_points", 0))
            minutes_value = stats.get("minutes", element.get("minutes"))
            try:
                points = float(points_value or 0)
            except (TypeError, ValueError):
                points = 0.0
            try:
                minutes = int(minutes_value) if minutes_value is not None else None
            except (TypeError, ValueError):
                minutes = None
            actuals[int(element["id"])] = _ActualPlayer(points=points, minutes=minutes)
        return actuals

    def _chip_for_gameweek(
        self,
        decision: HermesDecision,
        decision_document: Mapping[str, Any],
        gameweek: int,
        supplied_state: object | None,
        execution: ExecutionConfirmation | None = None,
    ) -> tuple[str | None, int | None, dict[str, object]]:
        if execution is not None:
            return execution.active_chip, execution.active_chip_set, {
                "chip": execution.active_chip,
                "set": execution.active_chip_set,
                "gameweek": gameweek,
                "source": "execution_confirmation",
            }
        for state, source in (
            (supplied_state, "argument"),
            (decision_document.get("chip_state"), "decision.chip_state"),
            (decision_document, "decision"),
        ):
            details = _chip_details(state, gameweek)
            if details is not None:
                chip, chip_set = details
                return chip, chip_set, {
                    "chip": chip, "set": chip_set, "gameweek": gameweek, "source": source,
                }
        try:
            from aifpl.chips import ChipStateStore

            state = ChipStateStore(self.root).latest(decision.season_id)
        except (FileNotFoundError, ValueError):
            state = None
        details = _chip_details(state, gameweek)
        if details is None:
            return None, None, {"chip": None, "set": None, "gameweek": gameweek, "source": "none"}
        chip, chip_set = details
        return chip, chip_set, {
            "chip": chip, "set": chip_set, "gameweek": gameweek, "source": "chip_state_store",
        }

    def _event_actuals(self, gameweek: int) -> dict[int, float]:
        return {element: actual.points for element, actual in self._event_player_stats(gameweek).items()}


def _unique_ids(player_ids: list[int]) -> list[int]:
    return list(dict.fromkeys(int(player_id) for player_id in player_ids))


def _validate_decision_lineup(
    decision: HermesDecision, xi_ids: list[int], vice_captain_id: int | None,
) -> None:
    squad_ids = _unique_ids(decision.squad.player_ids)
    if len(squad_ids) != 15 or set(xi_ids) - set(squad_ids) or len(xi_ids) != 11:
        raise ValueError("Decision must contain 15 players and 11 unique starting players")
    if decision.captain_id not in set(xi_ids):
        raise ValueError("Decision captain must be in the starting XI")
    vice_id = vice_captain_id
    if vice_id is not None and (vice_id not in set(xi_ids) or vice_id == decision.captain_id):
        raise ValueError("Decision vice-captain must be a different starting player")


def _pair_transfer_ids(
    outgoing: list[int], incoming: list[int], names: Mapping[int, tuple[str, str]],
) -> list[tuple[int, int]]:
    """Pair transfers by position, not by the arbitrary serialization order."""
    remaining = list(incoming)
    pairs: list[tuple[int, int]] = []
    for out_element in outgoing:
        out_position = names.get(out_element, ("", "?"))[1]
        match_index = next(
            (
                index for index, in_element in enumerate(remaining)
                if names.get(in_element, ("", "?"))[1] == out_position
            ),
            0,
        )
        if remaining:
            pairs.append((out_element, remaining.pop(match_index)))
    return pairs


def _ordered_bench_ids(
    decision_document: Mapping[str, Any], squad_ids: list[int], starting_ids: list[int],
) -> list[int]:
    squad = _unique_ids(squad_ids)
    starting = set(starting_ids)
    explicit: object | None = None
    containers = [
        decision_document,
        decision_document.get("squad", {}),
        decision_document.get("lineup", {}),
        decision_document.get("selection", {}),
    ]
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in ("bench_ids", "bench_order", "bench_player_ids", "substitute_ids", "bench", "substitutes"):
            if key in container:
                explicit = container[key]
                break
        if explicit is not None:
            break
    ordered: list[int] = []
    if isinstance(explicit, (list, tuple)):
        for player_id in explicit:
            try:
                identifier = (
                    int(player_id.get("id", player_id.get("element", player_id.get("player_id"))))
                    if isinstance(player_id, Mapping) else int(player_id)
                )
            except (AttributeError, TypeError, ValueError):
                continue
            if identifier in squad and identifier not in starting and identifier not in ordered:
                ordered.append(identifier)
    ordered.extend(
        player_id for player_id in squad
        if player_id not in starting and player_id not in ordered
    )
    return ordered


def _vice_captain_id(
    decision: HermesDecision, decision_document: Mapping[str, Any], gameweek: int,
) -> int | None:
    value = decision_document.get("vice_captain_id", decision.vice_captain_id)
    if value is None:
        for container_name in ("lineup", "selection", "squad"):
            container = decision_document.get(container_name)
            if isinstance(container, Mapping) and container.get("vice_captain_id") is not None:
                value = container["vice_captain_id"]
                break
    try:
        if value is not None:
            return int(value)
    except (TypeError, ValueError):
        pass
    plan = decision.horizon_plan
    if plan is not None:
        for week in plan.weeks:
            if week.gameweek == gameweek and week.vice_captain_id is not None:
                return int(week.vice_captain_id)
    return None


def _reconstruct_effective_xi(
    starting_ids: list[int],
    bench_ids: list[int],
    actuals: Mapping[int, _ActualPlayer],
    names: Mapping[int, tuple[str, str]],
) -> tuple[list[int], list[ScoredAutoSub]]:
    effective = list(starting_ids)
    used_bench: set[int] = set()
    autosubs: list[ScoredAutoSub] = []
    pending = [
        index for index, player_id in enumerate(starting_ids)
        if not _played(actuals.get(player_id))
    ]
    # FPL consumes the bench from first to last.  Each candidate is assigned
    # to the first still-empty starting slot for which the formation remains
    # legal; an ineligible candidate is skipped rather than stopping autosubs.
    for replacement_id in bench_ids:
        if replacement_id in used_bench or not _played(actuals.get(replacement_id)):
            continue
        replacement_index = next(
            (
                index for index in pending
                if _legal_substitution(effective, effective[index], replacement_id, names)
            ),
            None,
        )
        if replacement_index is None:
            continue
        outgoing_id = effective[replacement_index]
        effective[replacement_index] = replacement_id
        pending.remove(replacement_index)
        used_bench.add(replacement_id)
        autosubs.append(ScoredAutoSub(
            out_element=outgoing_id,
            in_element=replacement_id,
            out_name=names.get(outgoing_id, (f"#{outgoing_id}", "?"))[0],
            in_name=names.get(replacement_id, (f"#{replacement_id}", "?"))[0],
            out_actual=actuals.get(outgoing_id, _ActualPlayer(0.0, None)).points,
            in_actual=actuals.get(replacement_id, _ActualPlayer(0.0, None)).points,
        ))
    return effective, autosubs


def _legal_substitution(
    effective_xi: list[int], outgoing_id: int, incoming_id: int,
    names: Mapping[int, tuple[str, str]],
) -> bool:
    outgoing_position = names.get(outgoing_id, ("", "?"))[1]
    incoming_position = names.get(incoming_id, ("", "?"))[1]
    if outgoing_position == "GK" or incoming_position == "GK":
        return outgoing_position == incoming_position == "GK"
    if outgoing_position not in {"DEF", "MID", "FWD"} or incoming_position not in {"DEF", "MID", "FWD"}:
        return False
    positions = [names.get(player_id, ("", "?"))[1] for player_id in effective_xi]
    try:
        positions[positions.index(outgoing_position)] = incoming_position
    except ValueError:
        return False
    counts = {position: positions.count(position) for position in ("DEF", "MID", "FWD")}
    return (
        3 <= counts["DEF"] <= 5
        and 2 <= counts["MID"] <= 5
        and 1 <= counts["FWD"] <= 3
    )


def _played(actual: _ActualPlayer | None) -> bool:
    if actual is None:
        return False
    # Older locally persisted event snapshots only had total_points.  Treat
    # those rows as appearances so their scorecards remain readable; official
    # snapshots include minutes and take the strict path.
    return actual.minutes is None or actual.minutes > 0


def _scored_player(
    player_id: int,
    projections: Mapping[int, float],
    actuals: Mapping[int, _ActualPlayer],
    names: Mapping[int, tuple[str, str]],
    role: str,
    multiplier: int,
) -> ScoredPlayer:
    actual = actuals.get(player_id)
    return ScoredPlayer(
        element=player_id,
        name=names.get(player_id, (f"#{player_id}", "?"))[0],
        position=names.get(player_id, ("?", "?"))[1],
        projected=round(projections.get(player_id, 0.0), 4),
        actual=actual.points if actual is not None else 0.0,
        minutes=actual.minutes if actual is not None else None,
        played=_played(actual),
        role=role,
        multiplier=multiplier,
    )


def _chip_details(state: object | None, gameweek: int) -> tuple[str, int | None] | None:
    if state is None:
        return None
    if isinstance(state, (str, Path)):
        if isinstance(state, str):
            chip = _normalize_chip_name(state)
            if chip is not None:
                return chip, None
        path = Path(state)
        if not path.exists():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
    if hasattr(state, "model_dump"):
        state = state.model_dump()
    if not isinstance(state, Mapping):
        return None
    slots = state.get("slots")
    if isinstance(slots, list):
        for slot in slots:
            if not isinstance(slot, Mapping) or not slot.get("used"):
                continue
            try:
                used_gw = int(slot.get("used_gw"))
            except (TypeError, ValueError):
                continue
            if used_gw == gameweek and isinstance(slot.get("chip"), str):
                chip = _normalize_chip_name(slot["chip"])
                if chip is not None:
                    return chip, _optional_int(slot.get("set"))
    active_gameweek = _optional_int(
        state.get("active_gameweek", state.get("gameweek", state.get("used_gw")))
    )
    if active_gameweek is not None and active_gameweek != gameweek:
        return None
    value = state.get("active_chip", state.get("chip", state.get("chip_name", state.get("name"))))
    if isinstance(value, Mapping):
        chip_set = _optional_int(value.get("set", value.get("chip_set")))
        value = value.get("chip", value.get("name"))
    else:
        chip_set = _optional_int(state.get("active_chip_set", state.get("chip_set", state.get("set"))))
    if not isinstance(value, str) or not value:
        return None
    chip = _normalize_chip_name(value)
    return (chip, chip_set) if chip is not None else None


def _normalize_chip_name(value: str) -> str | None:
    normalized = value.strip().casefold().replace(" ", "_").replace("-", "_")
    return normalized if normalized in {"wildcard", "free_hit", "bench_boost", "triple_captain"} else None


def _transfer_hit(
    decision: HermesDecision,
    decision_document: Mapping[str, Any],
    gameweek: int,
    chip: str | None,
) -> tuple[int, int | None]:
    if chip in {"wildcard", "free_hit"} or decision.action == "adopt_initial":
        return 0, _optional_int(decision_document.get("free_transfers_before"))
    explicit_keys = ("transfer_hit", "hit_cost", "transfer_hit_cost", "points_hit")
    for key in explicit_keys:
        value = decision_document.get(key)
        parsed = _optional_int(value)
        if parsed is not None:
            return max(0, parsed), _optional_int(decision_document.get("free_transfers_before"))
    if decision.horizon_plan is not None:
        for week in decision.horizon_plan.weeks:
            if week.gameweek == gameweek:
                return max(0, int(week.hit_cost)), int(week.free_transfers_before)
    free_before = _optional_int(decision_document.get("free_transfers_before"))
    transfers_made = _optional_int(decision_document.get("transfers_made"))
    if transfers_made is None:
        transfers_made = len(decision.transfers_in)
    if free_before is not None:
        return max(0, transfers_made - free_before) * 4, free_before
    return 0, None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class CompletedDecisionScorer:
    """Persist final FPL results and score each completed, committed gameweek once."""

    def __init__(self, root: Path, fpl_client: FplClient | None = None) -> None:
        self.root = root
        self.fpl_client = fpl_client or FplClient()

    def score_pending(self, season_id: str) -> list[str]:
        from aifpl.hermes import HermesManager

        scorer = DecisionScorer(self.root)
        calibration = LiveCalibrationStore(self.root)
        latest_by_gameweek: dict[int, HermesDecision] = {}
        for decision in HermesManager(self.root).decisions():
            if decision.season_id == season_id:
                latest_by_gameweek.setdefault(decision.gameweek, decision)
        pending = []
        for decision in latest_by_gameweek.values():
            plan = decision.horizon_plan
            needs_score = not scorer.is_scored(decision.decision_path, decision.gameweek)
            needs_calibration = (
                plan is not None
                and bool(plan.projection_catalog)
                and (
                    calibration.needs_outcomes(season_id, decision.gameweek, plan.projection_catalog)
                    or calibration.needs_profile(season_id, decision.gameweek, plan.projection_catalog)
                )
            )
            if needs_score or needs_calibration:
                pending.append(decision)
        if not pending:
            return []

        bootstrap = asyncio.run(self.fpl_client.fetch_bootstrap())
        finalized_events = {
            event["id"]
            for event in bootstrap.get("events", [])
            if isinstance(event, dict)
            and isinstance(event.get("id"), int)
            and event.get("finished") is True
            and event.get("data_checked") is True
        }
        completed = [decision for decision in pending if decision.gameweek in finalized_events]
        if not completed:
            return []

        snapshots = SnapshotStore(self.root)
        bootstrap_path, _ = snapshots.save_bootstrap(bootstrap)
        score_paths: list[str] = []
        for decision in completed:
            event_path, _ = snapshots.save_event_live(
                decision.gameweek,
                asyncio.run(self.fpl_client.fetch_event_live(decision.gameweek)),
            )
            if not scorer.is_scored(decision.decision_path, decision.gameweek):
                score_paths.append(scorer.score(decision.decision_path, decision.gameweek).output_path)
            plan = decision.horizon_plan
            if plan is not None and plan.projection_catalog:
                if calibration.needs_outcomes(season_id, decision.gameweek, plan.projection_catalog):
                    calibration.record_outcomes(
                        season_id,
                        decision.gameweek,
                        plan.projection_catalog,
                        bootstrap_path,
                        event_path,
                        Path(decision.decision_path),
                        decision.created_at,
                    )
                if calibration.needs_profile(season_id, decision.gameweek, plan.projection_catalog):
                    applied_path = self.root / "normalized" / "current" / "odds_projections" / plan.projection_catalog
                    raw_path, _ = calibration._raw_catalog(applied_path)
                    rows = OddsProjectionStore(self.root).latest(raw_path.name)
                    methodology = {row.methodology for row in rows if row.gameweek == decision.gameweek}.pop()
                    calibration.build_profile(season_id, methodology, calibration._model_signature(raw_path))
        return score_paths
