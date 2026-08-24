from datetime import datetime, timezone

import pytest

from aifpl.artifacts import json_bytes, jsonl_bytes, write_immutable, write_manifest
from aifpl.live_calibration import LiveCalibrationStore, calibrated_odds_catalog, calibrated_odds_rows
from aifpl.odds_projections import OddsAdjustedGameweekProjection, OddsProjectionStore
from aifpl.snapshots import SnapshotStore


METHOD = "test_odds_v1"


def write_catalog(tmp_path, gameweek: int, predictions: list[float], player_prop_weight: float = 0.0, label: str = ""):
    rows = [
        OddsAdjustedGameweekProjection(
            player_id=index,
            player_name=f"Player {index}",
            position="MID",
            club=f"Club {index}",
            cost=50,
            gameweek=gameweek,
            fixture_count=1,
            odds_backed_fixture_count=1,
            projected_points=points,
            methodology=METHOD,
        )
        for index, points in enumerate(predictions, 1)
    ]
    path = tmp_path / "normalized" / "current" / "odds_projections" / f"gw{gameweek}-{gameweek}.test{gameweek}{label}.jsonl"
    write_immutable(path, jsonl_bytes(rows))
    write_manifest(
        tmp_path, path, artifact_type="odds_projections", created_at="2026-08-01T00:00:00Z",
        record_count=len(rows), sources={}, methodology=METHOD,
        parameters={
            "odds_coverage_status": "full",
            "odds_coverage_by_gameweek": {gameweek: 1.0},
            "odds_win_weight": 0.4,
            "player_prop_weight": player_prop_weight,
        },
    )
    return path


def write_final_snapshots(tmp_path, gameweek: int, actuals: list[float], finished: bool = True):
    store = SnapshotStore(tmp_path)
    bootstrap_path, _ = store.save_bootstrap(
        {
            "elements": [],
            "teams": [],
            "events": [{
                "id": gameweek,
                "deadline_time": f"2026-08-{10 + gameweek:02d}T18:00:00Z",
                "finished": finished,
                "data_checked": finished,
            }],
        },
        datetime(2026, 8, 20 + gameweek, tzinfo=timezone.utc),
    )
    event_path, _ = store.save_event_live(
        gameweek,
        {"elements": [{"id": index, "stats": {"total_points": points}} for index, points in enumerate(actuals, 1)]},
        datetime(2026, 8, 20 + gameweek, 1, tzinfo=timezone.utc),
    )
    return bootstrap_path, event_path


def configure_small_calibration(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_LIVE_CALIBRATION_WINDOW_GAMEWEEKS", "4")
    monkeypatch.setenv("AIFPL_LIVE_CALIBRATION_MIN_GAMEWEEKS", "2")
    monkeypatch.setenv("AIFPL_LIVE_CALIBRATION_MIN_OBSERVATIONS", "4")
    monkeypatch.setenv("AIFPL_LIVE_CALIBRATION_RECENCY_DECAY", "1")


def write_decision(tmp_path, gameweek: int):
    path = tmp_path / "hermes" / "decisions" / f"gw{gameweek}.json"
    write_immutable(path, json_bytes({}, pretty=True))
    return path, datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_live_calibration_warms_up_then_adjusts_only_future_catalogs(tmp_path, monkeypatch) -> None:
    configure_small_calibration(monkeypatch)
    store = LiveCalibrationStore(tmp_path)
    first_catalog = write_catalog(tmp_path, 1, [2.0, 4.0])
    first_bootstrap, first_event = write_final_snapshots(tmp_path, 1, [4.0, 8.0])
    first_decision, first_created_at = write_decision(tmp_path, 1)
    first_outcome = store.record_outcomes(
        "2026-27", 1, first_catalog.name, first_bootstrap, first_event, first_decision, first_created_at,
    )

    assert first_outcome is not None
    assert store.build_profile("2026-27", METHOD, first_outcome.model_signature).status == "warming_up"

    second_catalog = write_catalog(tmp_path, 2, [2.0, 4.0])
    second_bootstrap, second_event = write_final_snapshots(tmp_path, 2, [4.0, 8.0])
    second_decision, second_created_at = write_decision(tmp_path, 2)
    second_outcome = store.record_outcomes(
        "2026-27", 2, second_catalog.name, second_bootstrap, second_event, second_decision, second_created_at,
    )
    assert second_outcome is not None
    profile = store.build_profile("2026-27", METHOD, second_outcome.model_signature)

    future_catalog = write_catalog(tmp_path, 3, [3.0, 5.0])
    raw = OddsProjectionStore(tmp_path).latest(future_catalog.name)
    calibrated, applied = calibrated_odds_rows(tmp_path, future_catalog.name, "2026-27")
    materialized = calibrated_odds_catalog(tmp_path, future_catalog.name, "2026-27")
    historical, historical_profile = calibrated_odds_rows(tmp_path, second_catalog.name, "2026-27")

    assert profile.status == "active"
    assert applied is not None and applied.status == "active"
    assert raw[0].projected_points == 3.0
    assert calibrated[0].projected_points > raw[0].projected_points
    assert calibrated[0].methodology.endswith("rolling_affine_v1")
    assert materialized.catalog_id != future_catalog.name
    assert materialized.raw_catalog_id == future_catalog.name
    assert OddsProjectionStore(tmp_path).latest(materialized.catalog_id) == calibrated
    assert OddsProjectionStore(tmp_path).latest_path() == future_catalog
    assert historical_profile is None
    assert historical == OddsProjectionStore(tmp_path).latest(second_catalog.name)


def test_live_calibration_requires_official_finality(tmp_path, monkeypatch) -> None:
    configure_small_calibration(monkeypatch)
    catalog = write_catalog(tmp_path, 1, [2.0, 4.0])
    bootstrap_path, event_path = write_final_snapshots(tmp_path, 1, [4.0, 8.0], finished=False)
    decision_path, decision_created_at = write_decision(tmp_path, 1)

    with pytest.raises(ValueError, match="not marked final"):
        LiveCalibrationStore(tmp_path).record_outcomes(
            "2026-27", 1, catalog.name, bootstrap_path, event_path, decision_path, decision_created_at,
        )


def test_live_calibration_rejects_post_deadline_decisions(tmp_path, monkeypatch) -> None:
    configure_small_calibration(monkeypatch)
    catalog = write_catalog(tmp_path, 1, [2.0, 4.0])
    bootstrap_path, event_path = write_final_snapshots(tmp_path, 1, [4.0, 8.0])
    decision_path, _ = write_decision(tmp_path, 1)

    with pytest.raises(ValueError, match="not committed before"):
        LiveCalibrationStore(tmp_path).record_outcomes(
            "2026-27", 1, catalog.name, bootstrap_path, event_path, decision_path,
            datetime(2026, 8, 12, tzinfo=timezone.utc),
        )


def test_live_calibration_keeps_different_projection_configurations_separate(tmp_path, monkeypatch) -> None:
    configure_small_calibration(monkeypatch)
    first_catalog = write_catalog(tmp_path, 1, [2.0, 4.0], player_prop_weight=0.0, label="a")
    second_catalog = write_catalog(tmp_path, 1, [2.0, 4.0], player_prop_weight=0.5, label="b")
    bootstrap_path, event_path = write_final_snapshots(tmp_path, 1, [4.0, 8.0])
    first_decision, first_created_at = write_decision(tmp_path, 1)
    second_decision, second_created_at = write_decision(tmp_path, 2)
    store = LiveCalibrationStore(tmp_path)

    first = store.record_outcomes(
        "2026-27", 1, first_catalog.name, bootstrap_path, event_path, first_decision, first_created_at,
    )
    second = store.record_outcomes(
        "2026-27", 1, second_catalog.name, bootstrap_path, event_path, second_decision, second_created_at,
    )

    assert first is not None and second is not None
    assert first.model_signature != second.model_signature
