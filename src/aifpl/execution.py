from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from aifpl.artifacts import json_bytes, verify_artifact, write_immutable, write_manifest


class ExecutionConfirmation(BaseModel):
    """The team actually entered at the deadline, if manually or externally confirmed."""

    decision_path: str
    season_id: str
    gameweek: int = Field(ge=1, le=38)
    source: Literal["manual", "fpl_import"]
    squad_ids: list[int] = Field(min_length=15, max_length=15)
    starting_xi_ids: list[int] = Field(min_length=11, max_length=11)
    bench_ids: list[int] = Field(min_length=4, max_length=4)
    captain_id: int
    vice_captain_id: int
    transfers_out: list[int] = Field(default_factory=list)
    transfers_in: list[int] = Field(default_factory=list)
    hit_cost: int = Field(default=0, ge=0)
    free_transfers_before: int | None = Field(default=None, ge=0, le=5)
    pre_execution_squad_ids: list[int] | None = None
    active_chip: str | None = None
    active_chip_set: Literal[1, 2] | None = None
    pre_free_hit_squad_ids: list[int] | None = None
    pre_free_hit_bank: int | None = Field(default=None, ge=0)
    pre_free_hit_free_transfers: int | None = Field(default=None, ge=0, le=5)
    pre_free_hit_purchase_prices: dict[int, int] | None = None
    confirmed_at: datetime
    notes: str = ""
    output_path: str = ""


class ExecutionConfirmationError(ValueError):
    pass


class ExecutionConfirmationStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def confirm(
        self,
        decision_path: Path | str,
        *,
        source: Literal["manual", "fpl_import"] = "manual",
        squad_ids: list[int],
        starting_xi_ids: list[int],
        captain_id: int,
        vice_captain_id: int,
        bench_ids: list[int] | None = None,
        transfers_out: list[int] | None = None,
        transfers_in: list[int] | None = None,
        hit_cost: int = 0,
        free_transfers_before: int | None = None,
        pre_execution_squad_ids: list[int] | None = None,
        active_chip: str | None = None,
        active_chip_set: int | None = None,
        pre_free_hit_squad_ids: list[int] | None = None,
        pre_free_hit_bank: int | None = None,
        pre_free_hit_free_transfers: int | None = None,
        pre_free_hit_purchase_prices: dict[int, int] | None = None,
        notes: str = "",
        confirmed_at: datetime | None = None,
    ) -> ExecutionConfirmation:
        from aifpl.hermes import HermesDecision

        path = self._resolve_decision_path(decision_path)
        try:
            decision_document = json.loads(path.read_text(encoding="utf-8"))
            decision = HermesDecision.model_validate(decision_document)
        except (OSError, ValueError) as exc:
            raise ExecutionConfirmationError(f"Cannot load Hermes decision: {path}") from exc
        if not decision.season_id:
            raise ExecutionConfirmationError("The decision has no season ID")
        if decision.decision_path and not self._same_artifact(decision.decision_path, path):
            raise ExecutionConfirmationError("Decision path does not identify the supplied artifact")
        squad = _unique(squad_ids)
        starters = _unique(starting_xi_ids)
        if bench_ids is None:
            raise ExecutionConfirmationError("Execution confirmation must include the ordered bench IDs")
        bench = _unique(bench_ids)
        _validate_team(squad, starters, bench, captain_id, vice_captain_id)
        default_outgoing = [] if decision.action == "adopt_initial" else decision.transfers_out
        default_incoming = [] if decision.action == "adopt_initial" else decision.transfers_in
        outgoing_values = transfers_out if transfers_out is not None else default_outgoing
        incoming_values = transfers_in if transfers_in is not None else default_incoming
        outgoing = _unique(outgoing_values)
        incoming = _unique(incoming_values)
        if len(outgoing) != len(outgoing_values) or len(incoming) != len(incoming_values):
            raise ExecutionConfirmationError("Execution transfer lists must not contain duplicate players")
        if len(outgoing) != len(incoming):
            raise ExecutionConfirmationError("Execution transfer lists must have equal length")
        if active_chip is not None and active_chip not in {
            "wildcard", "free_hit", "bench_boost", "triple_captain",
        }:
            raise ExecutionConfirmationError("active_chip must be a supported FPL chip")
        if active_chip is None and active_chip_set is not None:
            raise ExecutionConfirmationError("active_chip_set requires an active_chip")
        if active_chip is not None and active_chip_set not in (1, 2):
            raise ExecutionConfirmationError("active_chip_set must be 1 or 2 when a chip is confirmed")
        if active_chip == "free_hit" and (
            pre_free_hit_squad_ids is None
            or pre_free_hit_bank is None
            or pre_free_hit_free_transfers is None
            or pre_free_hit_purchase_prices is None
        ):
            raise ExecutionConfirmationError(
                "Free Hit confirmation must preserve the pre-chip squad, bank, transfers, and purchase prices"
            )
        if pre_free_hit_squad_ids is not None and len(_unique(pre_free_hit_squad_ids)) != 15:
            raise ExecutionConfirmationError("Pre-Free Hit squad must contain 15 unique players")
        if pre_free_hit_purchase_prices is not None:
            if any(price < 0 for price in pre_free_hit_purchase_prices.values()):
                raise ExecutionConfirmationError("Pre-Free Hit purchase prices must not be negative")
            if pre_free_hit_squad_ids is not None and set(pre_free_hit_purchase_prices) != set(pre_free_hit_squad_ids):
                raise ExecutionConfirmationError("Pre-Free Hit purchase prices must cover the pre-chip squad")
        free_transfers_before = (
            free_transfers_before
            if free_transfers_before is not None
            else _decision_free_transfers_before(decision, decision_document)
        )
        _validate_execution_economics(
            decision,
            squad,
            outgoing,
            incoming,
            active_chip,
            hit_cost,
            free_transfers_before,
            pre_execution_squad_ids,
            pre_free_hit_squad_ids,
        )
        confirmed_at = confirmed_at or datetime.now(timezone.utc)
        if confirmed_at.tzinfo is None:
            raise ExecutionConfirmationError("confirmed_at must be timezone-aware")
        record = ExecutionConfirmation(
            decision_path=str(path),
            season_id=decision.season_id,
            gameweek=decision.gameweek,
            source=source,
            squad_ids=squad,
            starting_xi_ids=starters,
            bench_ids=bench,
            captain_id=captain_id,
            vice_captain_id=vice_captain_id,
            transfers_out=outgoing,
            transfers_in=incoming,
            hit_cost=hit_cost,
            free_transfers_before=free_transfers_before,
            active_chip=active_chip,
            active_chip_set=active_chip_set,
            pre_free_hit_squad_ids=_unique(pre_free_hit_squad_ids) if pre_free_hit_squad_ids is not None else None,
            pre_execution_squad_ids=_unique(pre_execution_squad_ids) if pre_execution_squad_ids is not None else None,
            pre_free_hit_bank=pre_free_hit_bank,
            pre_free_hit_free_transfers=pre_free_hit_free_transfers,
            pre_free_hit_purchase_prices=pre_free_hit_purchase_prices,
            confirmed_at=confirmed_at,
            notes=notes,
        )
        self._validate_chip(record)
        stamp = confirmed_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / "execution" / "confirmations" / record.season_id / f"gw{record.gameweek}" / f"{stamp}.json"
        record = record.model_copy(update={"output_path": str(path)})
        write_immutable(path, json_bytes(record.model_dump(mode="json"), pretty=True))
        write_manifest(
            self.root,
            path,
            artifact_type="execution_confirmation",
            created_at=confirmed_at.isoformat(),
            record_count=len(record.squad_ids),
            sources={"decision": Path(record.decision_path)},
            parameters={
                "season_id": record.season_id,
                "gameweek": record.gameweek,
                "source": record.source,
                "active_chip": record.active_chip,
            },
        )
        self._record_chip(record)
        return record

    def latest(self, season_id: str, gameweek: int) -> ExecutionConfirmation | None:
        directory = self.root / "execution" / "confirmations" / season_id / f"gw{gameweek}"
        paths = sorted(directory.glob("*.json")) if directory.exists() else []
        for path in reversed(paths):
            try:
                confirmation = self._read(path, season_id, gameweek)
                if confirmation is not None:
                    return confirmation
            except (OSError, ValueError):
                continue
        return None

    def latest_for_decision(
        self, decision_path: Path | str, season_id: str, gameweek: int,
    ) -> ExecutionConfirmation | None:
        candidate = Path(decision_path).resolve()
        directory = self.root / "execution" / "confirmations" / season_id / f"gw{gameweek}"
        paths = sorted(directory.glob("*.json")) if directory.exists() else []
        for path in reversed(paths):
            try:
                confirmation = self._read(path, season_id, gameweek)
                if confirmation is not None and Path(confirmation.decision_path).resolve() == candidate:
                    return confirmation
            except (OSError, ValueError):
                continue
        return None

    def _read(self, path: Path, season_id: str, gameweek: int) -> ExecutionConfirmation | None:
        verify_artifact(self.root, path, require_manifest=True)
        confirmation = ExecutionConfirmation.model_validate_json(path.read_text(encoding="utf-8"))
        if confirmation.season_id != season_id or confirmation.gameweek != gameweek:
            return None
        if not confirmation.output_path or Path(confirmation.output_path).resolve() != path.resolve():
            return None
        return confirmation

    def _resolve_decision_path(self, decision_path: Path | str) -> Path:
        candidate = Path(decision_path)
        path = candidate if candidate.is_absolute() else self.root / candidate
        try:
            if not path.resolve().is_relative_to(self.root.resolve()):
                raise ExecutionConfirmationError("decision_path must be below AIFPL_DATA_DIR")
        except OSError as exc:
            raise ExecutionConfirmationError("Cannot resolve decision_path") from exc
        if not path.is_file():
            raise ExecutionConfirmationError(f"Hermes decision does not exist: {decision_path}")
        return path.resolve()

    def _same_artifact(self, reference: str, path: Path) -> bool:
        candidate = Path(reference)
        candidates = [candidate] if candidate.is_absolute() else [self.root / candidate, self.root / "hermes" / "decisions" / candidate]
        return any(item.is_file() and item.resolve() == path.resolve() for item in candidates)

    def _record_chip(self, record: ExecutionConfirmation) -> None:
        if record.active_chip is None or record.active_chip_set is None:
            return
        from aifpl.chips import ChipStateStore

        store = ChipStateStore(self.root)
        current = store.latest(record.season_id)
        existing = next(
            (
                slot for slot in current.slots
                if slot.chip == record.active_chip and slot.set == record.active_chip_set
            ),
            None,
        )
        if existing is not None and existing.used and existing.used_gw == record.gameweek:
            return
        store.mark_used(record.season_id, record.active_chip, record.active_chip_set, record.gameweek)

    def _validate_chip(self, record: ExecutionConfirmation) -> None:
        if record.active_chip is None or record.active_chip_set is None:
            return
        from aifpl.chips import ChipStateStore

        store = ChipStateStore(self.root)
        current = store.latest(record.season_id)
        existing = next(
            (
                slot for slot in current.slots
                if slot.chip == record.active_chip and slot.set == record.active_chip_set
            ),
            None,
        )
        if existing is not None and existing.used and existing.used_gw == record.gameweek:
            return
        store.validate_activation(record.season_id, record.active_chip, record.active_chip_set, record.gameweek)


def _unique(values: list[int] | None) -> list[int]:
    return list(dict.fromkeys(int(value) for value in (values or [])))


def _validate_team(
    squad_ids: list[int], starters: list[int], bench: list[int],
    captain_id: int, vice_captain_id: int,
) -> None:
    if len(squad_ids) != 15:
        raise ExecutionConfirmationError("Confirmed squad must contain 15 unique players")
    if len(starters) != 11 or not set(starters) <= set(squad_ids):
        raise ExecutionConfirmationError("Confirmed starting XI must contain 11 squad players")
    if len(bench) != 4 or set(bench) != set(squad_ids) - set(starters):
        raise ExecutionConfirmationError("Bench order must contain exactly the four non-starting squad players")
    if captain_id not in set(starters):
        raise ExecutionConfirmationError("Captain must be in the starting XI")
    if vice_captain_id not in set(starters) or vice_captain_id == captain_id:
        raise ExecutionConfirmationError("Vice-captain must be a different starting player")


def _decision_free_transfers_before(decision: object, document: dict[str, object]) -> int | None:
    value = document.get("free_transfers_before")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    plan = getattr(decision, "horizon_plan", None)
    for week in getattr(plan, "weeks", []) if plan is not None else []:
        if week.gameweek == decision.gameweek:
            return week.free_transfers_before
    return None


def _validate_execution_economics(
    decision: object,
    squad: list[int],
    outgoing: list[int],
    incoming: list[int],
    active_chip: str | None,
    hit_cost: int,
    free_transfers_before: int | None,
    pre_execution_squad_ids: list[int] | None,
    pre_free_hit_squad_ids: list[int] | None,
) -> None:
    if set(outgoing) & set(incoming):
        raise ExecutionConfirmationError("A player cannot be both transferred out and in")
    if active_chip in {"wildcard", "free_hit"}:
        if hit_cost != 0:
            raise ExecutionConfirmationError("Wildcard and Free Hit executions cannot record a transfer hit")
    elif outgoing:
        if free_transfers_before is None:
            raise ExecutionConfirmationError("free_transfers_before is required for normal transfers")
        expected_hit = max(0, len(outgoing) - free_transfers_before) * 4
        if hit_cost != expected_hit:
            raise ExecutionConfirmationError(
                f"hit_cost must equal the official transfer cost ({expected_hit})"
            )
    elif hit_cost != 0:
        raise ExecutionConfirmationError("hit_cost must be zero when no transfers were confirmed")

    baseline_ids = pre_execution_squad_ids
    if active_chip == "free_hit":
        baseline_ids = pre_free_hit_squad_ids
    if baseline_ids is not None:
        baseline = _unique(baseline_ids)
        if len(baseline) != 15:
            raise ExecutionConfirmationError("Pre-execution squad must contain 15 unique players")
        if not set(outgoing) <= set(baseline) or set(incoming) & set(baseline):
            raise ExecutionConfirmationError("Execution transfers do not match the pre-execution squad")
        expected_squad = (set(baseline) - set(outgoing)) | set(incoming)
        if expected_squad != set(squad):
            raise ExecutionConfirmationError("Confirmed squad does not match its transfer delta")
    elif active_chip != "free_hit" and set(squad) != set(getattr(decision.squad, "player_ids", [])):
        raise ExecutionConfirmationError(
            "Confirmed squad differs from the decision; provide pre_execution_squad_ids to reconcile transfers"
        )
    if baseline_ids is None and (
        not set(outgoing) <= set(getattr(decision.squad, "player_ids", []))
        or set(outgoing) & set(squad)
        or not set(incoming) <= set(squad)
    ):
        raise ExecutionConfirmationError("Execution transfers do not match the decision or confirmed squad")
