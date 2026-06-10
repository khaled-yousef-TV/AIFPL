"""Unit tests for variability agent math."""

import pytest

from agents.variability_agent import (
    BLANK_POINTS,
    CAPTAIN_BLEND,
    HAUL_POINTS,
    MIN_APPEARANCES,
    captaincy_score,
    compute_variability_stats,
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
