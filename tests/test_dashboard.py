import json
from datetime import datetime, timezone

from aifpl.current import CurrentPlayer
from aifpl.dashboard import (
    _dashboard_captain_options,
    _dashboard_confidence,
    _dashboard_moves,
    _dashboard_player,
    _horizon_point,
)
from aifpl.hermes import (
    HermesDecision,
    HermesSquadState,
    HermesStrategy,
    HorizonPlanSnapshot,
    HorizonPlanWeekSnapshot,
)
from aifpl.odds_projections import OddsAdjustedGameweekProjection


def projection(player_id: int, gameweek: int, points: float, ownership: float = 0.0) -> OddsAdjustedGameweekProjection:
    return OddsAdjustedGameweekProjection(
        player_id, f"P{player_id}", "MID", "A", 50, gameweek, 1, 1, points,
        selected_by_percent=ownership,
    )


def decision(captain_id: int = 1, squad_ids: tuple[int, ...] = tuple(range(1, 16))) -> HermesDecision:
    return HermesDecision(
        action="execute_horizon", gameweek=1,
        squad=HermesSquadState(
            player_ids=list(squad_ids), bank=250, free_transfers=1,
            purchase_prices={player_id: 50 for player_id in squad_ids},
        ),
        captain_id=captain_id, starting_xi_ids=list(squad_ids[:11]),
        transfers_out=[], transfers_in=[], explanation="test",
        strategy=HermesStrategy(
            risk_tolerance=0.5, hit_aversion=0.5, differential_appetite=0.0,
            planning_horizon=3, rationale="test",
        ),
        model="test", created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        backend_methodology="test", decision_path="d.json",
    )


def test_horizon_snapshot_maps_to_dashboard_point() -> None:
    week = HorizonPlanWeekSnapshot(
        gameweek=3, transfers_made=1, free_transfers_before=2, hit_cost=4,
        bank_after=240, projected_points=60.0, net_projected_points=56.0,
        odds_coverage=0.75, unlimited_transfers=True, free_transfers_after=1,
        outgoing_ids=[9], incoming_ids=[18], captain_id=18,
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
    assert point.robustness_score == 0.0
    assert point.captain_id == 18
    assert point.free_transfers_after == 1
    assert point.unlimited_transfers is True
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
    player = CurrentPlayer(1, "Test", "MID", 1, "Arsenal", 100, "a", None, 0, 5, 100, 1900, 25, 5, 4, 9, 20, selected_by_percent=20.0)
    projection = OddsAdjustedGameweekProjection(
        1, "Test", "MID", "Arsenal", 100, 1, 1, 1, 6.0,
        expected_minutes=76.0, start_probability=1.0, availability_multiplier=1.0,
    )

    row = _dashboard_player(player, projection, True, False)

    assert row.expected_minutes == 76.0
    assert row.start_probability == 1.0
    assert row.availability_multiplier == 1.0
    assert row.value == 0.6
    assert row.differential_score == 4.8


def test_dashboard_moves_compute_horizon_gain_from_pinned_rows() -> None:
    snapshot = HorizonPlanSnapshot(
        projection_catalog="c.json", pre_season=False, solver_status="OPTIMAL", methodology="m",
        total_projected_points=120.0, total_hit_cost=4, total_net_projected_points=116.0,
        weeks=[
            HorizonPlanWeekSnapshot(gameweek=1, transfers_made=0, free_transfers_before=1, hit_cost=0, bank_after=250,
                                    projected_points=60.0, net_projected_points=60.0, odds_coverage=1.0),
            HorizonPlanWeekSnapshot(gameweek=2, transfers_made=1, free_transfers_before=1, hit_cost=0, bank_after=250,
                                    projected_points=60.0, net_projected_points=60.0, odds_coverage=1.0,
                                    outgoing_ids=[3], incoming_ids=[16], captain_id=16),
            HorizonPlanWeekSnapshot(gameweek=3, transfers_made=2, free_transfers_before=2, hit_cost=4, bank_after=250,
                                    projected_points=60.0, net_projected_points=56.0, odds_coverage=1.0,
                                    outgoing_ids=[4, 5], incoming_ids=[17, 18], captain_id=17),
        ],
    )
    rows = {}
    for gameweek in (1, 2, 3):
        rows[3, gameweek] = projection(3, gameweek, 5.0, 10.0)
        rows[16, gameweek] = projection(16, gameweek, 8.0, 20.0)
        rows[4, gameweek] = projection(4, gameweek, 4.0, 30.0)
        rows[17, gameweek] = projection(17, gameweek, 9.0, 40.0)
        rows[5, gameweek] = projection(5, gameweek, 3.0, 50.0)
        rows[18, gameweek] = projection(18, gameweek, 7.0, 60.0)

    moves = _dashboard_moves(snapshot, rows, {3: "Out3", 16: "In16", 4: "Out4", 17: "In17", 5: "Out5", 18: "In18"})

    assert [move.in_id for move in moves] == [16, 17, 18]
    assert moves[0].horizon_gain == 9.0
    assert moves[0].net_gain == 9.0
    assert moves[0].hit_cost == 0
    assert moves[0].in_ownership == 20.0
    assert moves[2].horizon_gain == 12.0
    assert moves[2].hit_cost == 4
    assert moves[2].net_gain == 10.0


def test_dashboard_captain_options_rank_squad_from_pinned_rows() -> None:
    rows = {
        (1, 1): projection(1, 1, 7.0),
        (2, 1): projection(2, 1, 5.0),
        (3, 1): projection(3, 1, 9.0),
    }

    options = _dashboard_captain_options(rows, decision(captain_id=1), {1: "A", 2: "B", 3: "C"})

    assert [option.player_id for option in options] == [3, 1, 2]
    assert options[1].is_captain is True
    assert options[0].projected_points == 9.0


def test_dashboard_captain_options_carry_participation_evidence() -> None:
    rows = {
        (1, 1): projection(1, 1, 7.0),
        (2, 1): projection(2, 1, 5.0),
        (3, 1): projection(3, 1, 9.0),
    }
    rows[(3, 1)] = OddsAdjustedGameweekProjection(
        3, "P3", "MID", "A", 50, 1, 1, 1, 9.0, expected_minutes=60.0, start_probability=0.5,
    )

    options = _dashboard_captain_options(rows, decision(captain_id=1), {1: "A", 2: "B", 3: "C"})

    assert options[0].expected_minutes == 60.0
    assert options[0].start_probability == 0.5
    assert options[0].projected_points == 9.0
