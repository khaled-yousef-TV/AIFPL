from aifpl.dashboard import _horizon_point
from aifpl.hermes import HorizonPlanWeekSnapshot


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
