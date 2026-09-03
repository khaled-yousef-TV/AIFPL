from datetime import datetime, timezone

import pytest

from aifpl.account import (
    AccountSnapshotStore,
    build_account_snapshot,
    derive_chips_remaining,
    derive_free_transfers,
)


def history() -> dict:
    return {
        "current": [
            {"event": 1, "overall_rank": 900_000, "bank": 40},
            {"event": 2, "overall_rank": 184_000, "bank": 83},
        ],
    }


def picks(event: int = 2) -> dict:
    return {
        "active_chip": None,
        "entry_history": {"event": event},
        "picks": [
            {
                "element": player_id,
                "position": player_id,
                "is_captain": player_id == 1,
                "is_vice_captain": player_id == 2,
            }
            for player_id in range(1, 16)
        ],
    }


def test_build_account_snapshot_creates_rank_game_state() -> None:
    snapshot, state = build_account_snapshot(
        history(), picks(), entry_id=123, season_id="2026-27", target_rank=50_000,
        free_transfers=2, chips_remaining={"wildcard": 1, "triple_captain": 1},
    )

    assert snapshot.gameweek == 2
    assert snapshot.overall_rank == 184_000
    assert snapshot.captain_id == 1
    assert snapshot.vice_captain_id == 2
    assert state.account_id == 123
    assert state.objective_mode == "RANK_MODE"
    assert state.strategy_status == "BEHIND_TARGET"
    assert [item.gameweek for item in state.rank_history] == [1, 2]


def test_account_snapshot_store_round_trips_with_manifest(tmp_path) -> None:
    snapshot, _ = build_account_snapshot(
        history(), picks(), entry_id=123, season_id="2026-27", target_rank=50_000,
        free_transfers=2, fetched_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    path = AccountSnapshotStore(tmp_path).save(snapshot)
    loaded = AccountSnapshotStore(tmp_path).latest(entry_id=123, season_id="2026-27")

    assert loaded == snapshot.model_copy(update={"output_path": str(path)})
    assert path.with_suffix(".manifest.json").is_file()


def test_account_import_rejects_history_and_picks_gameweek_mismatch() -> None:
    with pytest.raises(ValueError, match="gameweeks do not match"):
        build_account_snapshot(
            history(), picks(event=1), entry_id=123, season_id="2026-27", target_rank=50_000,
            free_transfers=2,
        )


def test_account_import_rejects_missing_captain() -> None:
    payload = picks()
    payload["picks"][0]["is_captain"] = False

    with pytest.raises(ValueError, match="one captain"):
        build_account_snapshot(
            history(), payload, entry_id=123, season_id="2026-27", target_rank=50_000,
            free_transfers=2,
        )


def test_account_import_derives_free_transfers_and_chips_from_history() -> None:
    account_history = {
        "current": [
            {"event": 1, "overall_rank": 900_000, "bank": 40, "event_transfers": 0},
            {"event": 2, "overall_rank": 184_000, "bank": 83, "event_transfers": 2},
        ],
        "chips": [{"name": "wildcard"}, {"name": "freehit"}],
    }

    assert derive_free_transfers(account_history["current"], 2) == 1
    remaining = derive_chips_remaining(account_history)
    assert remaining == {
        "wildcard": 1,
        "free_hit": 1,
        "bench_boost": 1,
        "triple_captain": 1,
    }


def test_account_import_uses_derived_account_values_when_not_overridden() -> None:
    account_history = history()
    account_history["current"][0]["event_transfers"] = 0
    account_history["current"][1]["event_transfers"] = 2
    account_history["chips"] = [{"name": "wildcard"}]

    snapshot, state = build_account_snapshot(
        account_history,
        picks(),
        entry_id=123,
        season_id="2026-27",
        target_rank=50_000,
    )

    assert snapshot.free_transfers == 1
    assert state.free_transfers == 1
    assert snapshot.chips_remaining["wildcard"] == 1
    assert snapshot.chips_remaining["free_hit"] == 1


def test_free_transfer_reconstruction_handles_gw1_roll_and_consecutive_rolls() -> None:
    records = [
        {"event": 1, "event_transfers": 0},
        {"event": 2, "event_transfers": 0},
        {"event": 3, "event_transfers": 0},
    ]

    assert derive_free_transfers(records, 1) == 1
    assert derive_free_transfers(records, 2) == 2
    assert derive_free_transfers(records, 3) == 3


def test_free_transfer_reconstruction_handles_paid_transfers_and_hits() -> None:
    records = [
        {"event": 1, "event_transfers": 0},
        {"event": 2, "event_transfers": 3},
    ]

    assert derive_free_transfers(records, 2) == 1


@pytest.mark.parametrize("chip", ["wildcard", "free_hit"])
def test_free_transfer_reconstruction_preserves_banked_transfer_on_chip(chip: str) -> None:
    records = [
        {"event": 1, "event_transfers": 0},
        {"event": 2, "event_transfers": 12},
    ]

    assert derive_free_transfers(records, 2, chip_events={2: chip}) == 1


@pytest.mark.parametrize("chip", ["wildcard", "free_hit"])
@pytest.mark.parametrize("entering_free_transfers", [1, 2, 3, 5])
def test_free_transfer_reconstruction_preserves_each_chip_entry_balance(
    chip: str, entering_free_transfers: int,
) -> None:
    records = [
        {"event": 1, "event_transfers": 0},
        {"event": 2, "event_transfers": 15},
    ]

    assert derive_free_transfers(
        records,
        2,
        initial_free_transfers=entering_free_transfers - 1,
        chip_events={2: chip},
    ) == entering_free_transfers


def test_free_transfer_reconstruction_caps_at_five() -> None:
    records = [{"event": event, "event_transfers": 0} for event in range(1, 8)]

    assert derive_free_transfers(records, 7) == 5


def test_account_reconciliation_keeps_internal_squad_separate_from_public_state() -> None:
    public_ids = list(range(1, 16))
    internal_ids = [*range(1, 15), 16]
    snapshot, state = build_account_snapshot(
        history(), picks(), entry_id=123, season_id="2026-27", target_rank=50_000,
        free_transfers=2, internal_squad_ids=internal_ids, internal_gameweek=3,
        reconciliation_source="execution_confirmation:test.json",
    )

    assert snapshot.squad_ids == public_ids
    assert snapshot.internal_squad_ids == internal_ids
    assert snapshot.reconciliation_status == "not_comparable"
    assert state.internal_squad_ids == internal_ids
    assert state.internal_squad_gameweek == 3


def test_account_reconciliation_flags_same_gameweek_mismatch() -> None:
    internal_ids = [*range(1, 15), 16]
    snapshot, state = build_account_snapshot(
        history(), picks(), entry_id=123, season_id="2026-27", target_rank=50_000,
        free_transfers=2, internal_squad_ids=internal_ids, internal_gameweek=2,
        reconciliation_source="execution_confirmation:test.json",
    )

    assert snapshot.reconciliation_status == "mismatch"
    assert snapshot.reconciliation_missing_player_ids == [16]
    assert snapshot.reconciliation_unexpected_player_ids == [15]
    assert state.account_reconciliation_status == "mismatch"
