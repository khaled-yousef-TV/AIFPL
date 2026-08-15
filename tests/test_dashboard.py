import json

from aifpl.current import CurrentPlayer
from aifpl.dashboard import _dashboard_confidence, _dashboard_player, _horizon_point
from aifpl.hermes import HorizonPlanWeekSnapshot
from aifpl.odds_projections import OddsAdjustedGameweekProjection


def test_horizon_snapshot_maps_to_dashboard_point() -> None:
    week = HorizonPlanWeekSnapshot(
        gameweek=3, transfers_made=1, free_transfers_before=2, hit_cost=4,
        bank_after=240, projected_points=60.0, net_projected_points=56.0,
        odds_coverage=0.75, outgoing_ids=[9], incoming_ids=[18], captain_id=18,
        starting_xi_ids=[1], squad_ids=[1, 18],
    )

    point = _horizon_point(week, {9: "Player 9", 18: "Player 18"})

    assert point.gameweek == 3
    assert point.projected_points == 60.0
    assert point.net_projected_points == 56.0
    assert point.hit_cost == 4
    assert point.transfers_made == 1
    assert point.free_transfers_before == 2
    assert point.bank_after == 240
    assert point.odds_coverage == 0.75
    assert point.captain_id == 18
    assert point.transfers[0].out_name == "Player 9"
    assert point.transfers[0].in_name == "Player 18"


def test_horizon_snapshot_week_without_transfers_has_empty_list() -> None:
    week = HorizonPlanWeekSnapshot(
        gameweek=1, transfers_made=0, free_transfers_before=5, hit_cost=0,
        bank_after=250, projected_points=55.0, net_projected_points=55.0,
        odds_coverage=1.0,
    )

    point = _horizon_point(week, {})

    assert point.transfers == []
    assert point.hit_cost == 0


def test_dashboard_confidence_reads_odds_coverage_manifest(tmp_path) -> None:
    catalog_dir = tmp_path / "normalized" / "current" / "odds_projections"
    catalog_dir.mkdir(parents=True)
    catalog_id = "gw1-2.20260811T000000000001Z.fpl_xg_xa_blend_v2.jsonl"
    manifest_id = "gw1-2.20260811T000000000001Z.fpl_xg_xa_blend_v2.manifest.json"
    (catalog_dir / catalog_id).write_text("")
    (catalog_dir / manifest_id).write_text(json.dumps({
        "parameters": {
            "odds_coverage_by_gameweek": {"1": 1.0, "2": 0.0},
            "odds_coverage_status": "partial",
            "evidence_cutoff": "2026-08-11T00:00:00+00:00",
            "max_evidence_age_hours": 168,
        },
    }))

    confidence = _dashboard_confidence(tmp_path)

    assert confidence.status == "partial"
    assert confidence.calibration == "uncalibrated"
    assert confidence.odds_coverage_by_gameweek == {1: 1.0, 2: 0.0}
    assert confidence.projection_catalog == catalog_id
    assert confidence.evidence_cutoff is not None


def test_dashboard_confidence_is_unknown_without_manifest(tmp_path) -> None:
    confidence = _dashboard_confidence(tmp_path)

    assert confidence.status == "unknown"
    assert confidence.odds_coverage_by_gameweek == {}
    assert confidence.projection_catalog is None


def test_dashboard_player_carries_participation_evidence() -> None:
    player = CurrentPlayer(1, "Test", "MID", 1, "Arsenal", 100, "a", None, 0, 5, 100, 1900, 25, 5, 4, 9, 20)
    projection = OddsAdjustedGameweekProjection(
        1, "Test", "MID", "Arsenal", 100, 1, 1, 1, 6.0,
        expected_minutes=76.0, start_probability=1.0, availability_multiplier=1.0,
    )

    row = _dashboard_player(player, projection, True, False)

    assert row.expected_minutes == 76.0
    assert row.start_probability == 1.0
    assert row.availability_multiplier == 1.0
