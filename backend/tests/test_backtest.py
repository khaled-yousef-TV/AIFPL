"""Tests for the backtest harness and season helpers."""

from hermes.backtest import (
    actual_points_at,
    backtest_gameweek,
    reconstruct_player_stats,
    run_backtest,
)


def make_history(points_by_round, minutes=90):
    return [
        {"round": r, "total_points": p, "minutes": minutes if p is not None else 0}
        for r, p in points_by_round.items()
    ]


def test_reconstruct_uses_only_prior_rounds():
    history = make_history({1: 2, 2: 9, 3: 4, 4: 6, 5: 12, 6: 3, 7: 99})
    stats = reconstruct_player_stats(history, before_gw=7)
    assert stats is not None
    assert stats["n_gws"] == 6           # round 7 excluded
    assert stats["form_recent"] == (4 + 6 + 12 + 3) / 4


def test_reconstruct_requires_min_appearances():
    history = make_history({1: 5, 2: 6})
    assert reconstruct_player_stats(history, before_gw=3) is None


def test_actual_points_handles_dgw_and_blanks():
    history = make_history({10: 7}) + [
        {"round": 12, "total_points": 5, "minutes": 90},
        {"round": 12, "total_points": 3, "minutes": 60},   # DGW second fixture
        {"round": 13, "total_points": 0, "minutes": 0},    # didn't play
    ]
    assert actual_points_at(history, 10) == 7
    assert actual_points_at(history, 12) == 8             # both fixtures summed
    assert actual_points_at(history, 13) is None
    assert actual_points_at(history, 14) is None


def _synthetic_archive(n_players=40, rounds=12):
    """Players with deterministic point patterns so strategies are scoreable."""
    archive = []
    for i in range(n_players):
        base = (i % 8) + 2
        history = make_history({r: base + (r + i) % 5 for r in range(1, rounds + 1)})
        archive.append({
            "player_name": f"P{i}",
            "gw_history": history,
        })
    return archive


def test_backtest_gameweek_scores_strategies():
    result = backtest_gameweek(_synthetic_archive(), gw=10)
    assert result is not None
    assert result["players_scored"] >= 30
    assert result["league_avg_points"] > 0
    assert "captain_by_ceiling" in result and "captain_by_mean" in result
    assert result["best_possible_captain"]["actual"] >= result["captain_by_ceiling"]["actual"]


def test_backtest_gameweek_returns_none_when_too_few_players():
    assert backtest_gameweek(_synthetic_archive(n_players=5), gw=10) is None


def test_run_backtest_aggregates():
    result = run_backtest(_synthetic_archive(), start_gw=8, end_gw=12)
    summary = result["summary"]
    assert summary["gameweeks_scored"] == 5
    assert summary["captaincy"]["best_possible_avg"] >= summary["captaincy"]["by_ceiling_avg"]
    assert "edge_vs_league" in summary["form_signal"]


def test_previous_season_helper():
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.season_archive_service import get_previous_season
    assert get_previous_season("2026-27") == "2025-26"
    assert get_previous_season("2030-31") == "2029-30"
