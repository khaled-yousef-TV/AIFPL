"""Unit tests for variability agent math."""

import pytest

from agents.variability_agent import (
    BLANK_POINTS,
    CAPTAIN_BLEND,
    CAPTAIN_DEVIATION_THRESHOLD,
    HAUL_POINTS,
    MIN_APPEARANCES,
    captaincy_score,
    compute_variability_stats,
    pick_captain_anchored,
    season_points_proxy,
)


def test_captaincy_score_is_mean_dominant():
    # A high-mean steady player should outrank a low-mean high-ceiling punt
    steady = {"mean_pts": 7.0, "form_recent": 7.0, "ceiling_p90": 10.0}
    punt = {"mean_pts": 3.0, "form_recent": 3.0, "ceiling_p90": 18.0}
    assert captaincy_score(steady) > captaincy_score(punt)


def test_captaincy_blend_weights_sum_to_one():
    assert abs(sum(CAPTAIN_BLEND.values()) - 1.0) < 1e-9


def test_captaincy_score_rewards_form_and_ceiling_as_tiebreak():
    base = {"mean_pts": 6.0, "form_recent": 4.0, "ceiling_p90": 8.0}
    hotter = {"mean_pts": 6.0, "form_recent": 9.0, "ceiling_p90": 8.0}
    higher_ceiling = {"mean_pts": 6.0, "form_recent": 4.0, "ceiling_p90": 15.0}
    assert captaincy_score(hotter) > captaincy_score(base)
    assert captaincy_score(higher_ceiling) > captaincy_score(base)


def test_compute_stats_includes_form_recent():
    stats = compute_variability_stats([2, 4, 6, 8, 10, 12])
    # last 4 of [2,4,6,8,10,12] -> [6,8,10,12] mean 9.0
    assert stats["form_recent"] == 9.0


def test_too_few_appearances_returns_none():
    assert compute_variability_stats([5] * (MIN_APPEARANCES - 1)) is None
    assert compute_variability_stats([]) is None


def test_steady_player_has_zero_variance_and_max_consistency():
    stats = compute_variability_stats([6, 6, 6, 6, 6, 6])
    assert stats["mean_pts"] == 6.0
    assert stats["stddev"] == 0.0
    assert stats["cv"] == 0.0
    assert stats["consistency_score"] == 1.0
    assert stats["ceiling_p90"] == 6.0
    assert stats["floor_p10"] == 6.0
    assert stats["haul_rate"] == 0.0
    assert stats["blank_rate"] == 0.0


def test_boom_bust_player():
    # Half hauls, half blanks: classic high-ceiling captaincy profile
    points = [15, 1, 16, 2, 14, 1, 17, 2, 15, 1]
    stats = compute_variability_stats(points)
    assert stats["n_gws"] == 10
    assert stats["haul_rate"] == 0.5
    assert stats["blank_rate"] == 0.5
    assert stats["ceiling_p90"] >= 15
    assert stats["floor_p10"] <= 2
    assert stats["cv"] > 0.5
    assert stats["consistency_score"] < 0.7


def test_haul_and_blank_thresholds_are_inclusive():
    points = [HAUL_POINTS, BLANK_POINTS, 5, 5, 5, 5]
    stats = compute_variability_stats(points)
    assert stats["haul_rate"] == pytest.approx(1 / 6, abs=1e-3)
    assert stats["blank_rate"] == pytest.approx(1 / 6, abs=1e-3)


def test_consistency_ordering():
    steady = compute_variability_stats([5, 6, 5, 6, 5, 6])
    spiky = compute_variability_stats([1, 12, 0, 14, 2, 4])
    assert steady["consistency_score"] > spiky["consistency_score"]
    assert spiky["ceiling_p90"] > steady["ceiling_p90"]


# ---- anchored captaincy (baseline + decisive-deviation) ----

def _cand(name, season_pts, mean, form, ceiling):
    return {
        "name": name, "season_pts": season_pts,
        "mean_pts": mean, "form_recent": form, "ceiling_p90": ceiling,
    }


def test_anchored_defaults_to_season_points_leader():
    # Challenger's blend edge is positive but NOT decisive -> stay with baseline
    baseline = _cand("Leader", season_pts=200, mean=6.0, form=6.0, ceiling=12.0)
    rival = _cand("Rival", season_pts=150, mean=6.5, form=7.0, ceiling=13.0)
    pick = pick_captain_anchored([baseline, rival])
    assert pick["name"] == "Leader"


def test_anchored_deviates_on_decisive_blend_edge():
    baseline = _cand("Leader", season_pts=200, mean=5.0, form=4.0, ceiling=10.0)
    rival = _cand("Rival", season_pts=150, mean=8.0, form=9.0, ceiling=16.0)
    edge = captaincy_score(rival) - captaincy_score(baseline)
    assert edge >= CAPTAIN_DEVIATION_THRESHOLD  # sanity: this case IS decisive
    pick = pick_captain_anchored([baseline, rival])
    assert pick["name"] == "Rival"


def test_anchored_can_never_pick_worse_than_baseline_without_decisive_edge():
    # threshold=inf must reproduce the naive baseline exactly
    cands = [
        _cand("A", 180, 7.0, 8.0, 14.0),
        _cand("B", 220, 6.0, 5.0, 11.0),
        _cand("C", 90, 9.0, 9.5, 18.0),
    ]
    pick = pick_captain_anchored(cands, threshold=float("inf"))
    assert pick["name"] == "B"


def test_anchored_empty_candidates():
    assert pick_captain_anchored([]) is None


def test_season_points_proxy_uses_exact_when_present():
    assert season_points_proxy({"season_pts": 123, "mean_pts": 5, "n_gws": 10}) == 123
    # Falls back to appearance-sum (mean x appearances) otherwise
    assert season_points_proxy({"mean_pts": 5.0, "n_gws": 10}) == 50.0
