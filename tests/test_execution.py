from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from aifpl.artifacts import json_bytes, write_immutable
from aifpl.account import latest_internal_squad_context
from aifpl.chips import ChipStateStore
from aifpl.execution import ExecutionConfirmationError, ExecutionConfirmationStore
from aifpl.hermes import HermesDecision, HermesSquadState, HermesStrategy


def _strategy() -> HermesStrategy:
    return HermesStrategy(
        risk_tolerance=0.4, hit_aversion=0.8, differential_appetite=0.3,
        planning_horizon=4, preferred_players=[], rationale="test",
    )


def write_decision(tmp_path: Path, gameweek: int = 1) -> Path:
    decision_path = tmp_path / "hermes" / "decisions" / "decision.json"
    decision = HermesDecision(
        action="hold", gameweek=gameweek,
        squad=HermesSquadState(
            player_ids=list(range(1, 16)), bank=20, free_transfers=1,
            purchase_prices={player_id: 50 for player_id in range(1, 16)},
        ),
        captain_id=1, vice_captain_id=2, starting_xi_ids=list(range(1, 12)),
        transfers_out=[], transfers_in=[], explanation="test",
        strategy=_strategy(), model="test-model",
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        backend_methodology="test", decision_path=str(decision_path),
        state_path=str(tmp_path / "hermes" / "states" / "state.json"),
        season_id="2026-27",
    )
    write_immutable(decision_path, json_bytes(decision.model_dump(mode="json"), pretty=True))
    return decision_path


def _team() -> tuple[list[int], list[int], list[int]]:
    return list(range(1, 16)), list(range(1, 12)), [12, 13, 14, 15]


def test_confirmation_is_immutable_and_reconciles_to_decision(tmp_path) -> None:
    decision_path = write_decision(tmp_path)
    squad, starters, bench = _team()

    record = ExecutionConfirmationStore(tmp_path).confirm(
        decision_path,
        squad_ids=squad,
        starting_xi_ids=starters,
        bench_ids=bench,
        captain_id=1,
        vice_captain_id=2,
        notes="Entered manually before deadline",
        confirmed_at=datetime(2026, 8, 14, 17, 0, tzinfo=timezone.utc),
    )

    assert record.source == "manual"
    assert record.output_path.endswith("/execution/confirmations/2026-27/gw1/20260814T170000000000Z.json")
    assert Path(record.output_path).is_file()
    assert ExecutionConfirmationStore(tmp_path).latest_for_decision(
        decision_path, "2026-27", 1,
    ) == record
    assert ExecutionConfirmationStore(tmp_path).latest_for_season("2026-27") == record
    internal_ids, internal_gameweek, source = latest_internal_squad_context(tmp_path, "2026-27")
    assert internal_ids == squad
    assert internal_gameweek == 1
    assert source is not None and source.startswith("execution_confirmation:")


def test_confirmation_records_chip_usage_once(tmp_path) -> None:
    decision_path = write_decision(tmp_path)
    squad, starters, bench = _team()

    ExecutionConfirmationStore(tmp_path).confirm(
        decision_path,
        squad_ids=squad,
        starting_xi_ids=starters,
        bench_ids=bench,
        captain_id=1,
        vice_captain_id=2,
        active_chip="triple_captain",
        active_chip_set=1,
    )

    state = ChipStateStore(tmp_path).latest("2026-27")
    slot = next(slot for slot in state.slots if slot.chip == "triple_captain" and slot.set == 1)
    assert slot.used is True
    assert slot.used_gw == 1


def test_free_hit_confirmation_requires_restoration_state(tmp_path) -> None:
    decision_path = write_decision(tmp_path)
    squad, starters, bench = _team()

    with pytest.raises(ExecutionConfirmationError, match="pre-chip squad"):
        ExecutionConfirmationStore(tmp_path).confirm(
            decision_path,
            squad_ids=squad,
            starting_xi_ids=starters,
            bench_ids=bench,
            captain_id=1,
            vice_captain_id=2,
            active_chip="free_hit",
            active_chip_set=1,
        )


def test_free_hit_execution_context_uses_restored_squad_for_reconciliation(tmp_path) -> None:
    decision_path = write_decision(tmp_path, gameweek=2)
    baseline, _, _ = _team()
    temporary = [player_id for player_id in baseline if player_id != 5] + [16]

    ExecutionConfirmationStore(tmp_path).confirm(
        decision_path,
        squad_ids=temporary,
        starting_xi_ids=[1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12],
        bench_ids=[13, 14, 15, 16],
        captain_id=1,
        vice_captain_id=2,
        transfers_out=[5],
        transfers_in=[16],
        active_chip="free_hit",
        active_chip_set=1,
        pre_free_hit_squad_ids=baseline,
        pre_free_hit_bank=20,
        pre_free_hit_free_transfers=1,
        pre_free_hit_purchase_prices={player_id: 50 for player_id in baseline},
    )

    internal_ids, internal_gameweek, _ = latest_internal_squad_context(tmp_path, "2026-27")

    assert internal_ids == baseline
    assert internal_gameweek == 2


def test_confirmation_rejects_invalid_lineup(tmp_path) -> None:
    decision_path = write_decision(tmp_path)

    with pytest.raises(ExecutionConfirmationError, match="Captain"):
        ExecutionConfirmationStore(tmp_path).confirm(
            decision_path,
            squad_ids=list(range(1, 16)),
            starting_xi_ids=list(range(1, 12)),
            bench_ids=[12, 13, 14, 15],
            captain_id=15,
            vice_captain_id=2,
        )


def test_confirmation_requires_ordered_bench_and_reconciles_transfer_delta(tmp_path) -> None:
    decision_path = write_decision(tmp_path)
    squad, starters, _ = _team()

    with pytest.raises(ExecutionConfirmationError, match="ordered bench"):
        ExecutionConfirmationStore(tmp_path).confirm(
            decision_path,
            squad_ids=squad,
            starting_xi_ids=starters,
            captain_id=1,
            vice_captain_id=2,
        )

    confirmed_squad = [player_id for player_id in squad if player_id != 5] + [16]
    confirmed_starters = [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12]
    confirmed_bench = [13, 14, 15, 16]
    with pytest.raises(ExecutionConfirmationError, match="transfer delta"):
        ExecutionConfirmationStore(tmp_path).confirm(
            decision_path,
            squad_ids=squad,
            starting_xi_ids=starters,
            bench_ids=[12, 13, 14, 15],
            captain_id=1,
            vice_captain_id=2,
            transfers_out=[5],
            transfers_in=[16],
            free_transfers_before=1,
            pre_execution_squad_ids=squad,
        )

    record = ExecutionConfirmationStore(tmp_path).confirm(
        decision_path,
        squad_ids=confirmed_squad,
        starting_xi_ids=confirmed_starters,
        bench_ids=confirmed_bench,
        captain_id=1,
        vice_captain_id=2,
        transfers_out=[5],
        transfers_in=[16],
        free_transfers_before=1,
        pre_execution_squad_ids=squad,
    )
    assert record.squad_ids == confirmed_squad
    assert record.hit_cost == 0


def test_confirmation_recomputes_normal_transfer_hit(tmp_path) -> None:
    decision_path = write_decision(tmp_path)
    squad, _, _ = _team()
    confirmed_squad = [player_id for player_id in squad if player_id != 5] + [16]

    with pytest.raises(ExecutionConfirmationError, match="official transfer cost"):
        ExecutionConfirmationStore(tmp_path).confirm(
            decision_path,
            squad_ids=confirmed_squad,
            starting_xi_ids=[1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12],
            bench_ids=[13, 14, 15, 16],
            captain_id=1,
            vice_captain_id=2,
            transfers_out=[5],
            transfers_in=[16],
            free_transfers_before=0,
            hit_cost=0,
            pre_execution_squad_ids=squad,
        )


def test_confirmation_rejects_transfer_lists_that_do_not_match_final_squad(tmp_path) -> None:
    decision_path = write_decision(tmp_path)
    squad, starters, bench = _team()

    with pytest.raises(ExecutionConfirmationError, match="confirmed squad"):
        ExecutionConfirmationStore(tmp_path).confirm(
            decision_path,
            squad_ids=squad,
            starting_xi_ids=starters,
            bench_ids=bench,
            captain_id=1,
            vice_captain_id=2,
            transfers_out=[1],
            transfers_in=[16],
            free_transfers_before=1,
        )


def test_confirmation_reads_require_a_valid_manifest_hash(tmp_path) -> None:
    decision_path = write_decision(tmp_path)
    squad, starters, bench = _team()
    record = ExecutionConfirmationStore(tmp_path).confirm(
        decision_path,
        squad_ids=squad,
        starting_xi_ids=starters,
        bench_ids=bench,
        captain_id=1,
        vice_captain_id=2,
    )
    Path(record.output_path).write_text(
        Path(record.output_path).read_text(encoding="utf-8").replace('"captain_id": 1', '"captain_id": 2'),
        encoding="utf-8",
    )

    assert ExecutionConfirmationStore(tmp_path).latest("2026-27", 1) is None
