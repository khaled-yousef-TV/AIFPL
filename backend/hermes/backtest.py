"""
Backtest harness: replay past gameweeks from the SeasonArchive.

For each GW N, reconstruct what the agents would have known using only
rounds < N (form, mean, ceiling/floor), then score the core strategies
against what actually happened in GW N:

- captaincy-by-ceiling (variability agent's TC heuristic)
- captaincy-by-mean (baseline)
- hot-form top picks vs league average (form agent's heuristic)
- consistency core vs league average

Scope note: ownership, betting odds and news are NOT historically
available, so this validates the deterministic data/form/variability
core — not the news/betting agents or the LLM layer.
"""

import logging
from typing import Dict, List, Optional

from agents.variability_agent import MIN_APPEARANCES, compute_variability_stats

logger = logging.getLogger(__name__)

FORM_WINDOW = 4          # recent GWs for the form signal
TOP_N_STRATEGY = 10      # players per strategy bucket
MIN_PRIOR_APPEARANCES = MIN_APPEARANCES


def reconstruct_player_stats(gw_history: List[Dict], before_gw: int) -> Optional[Dict]:
    """
    Compute the stats an agent would have had BEFORE before_gw
    (appearances only, rounds < before_gw).
    """
    prior = [
        h for h in gw_history
        if h.get("round", 0) < before_gw and h.get("minutes", 0) > 0
    ]
    if len(prior) < MIN_PRIOR_APPEARANCES:
        return None

    points = [h.get("total_points", 0) for h in prior]
    stats = compute_variability_stats(points)
    if stats is None:
        return None
    stats["form_recent"] = round(
        sum(points[-FORM_WINDOW:]) / min(len(points), FORM_WINDOW), 2
    )
    return stats


def actual_points_at(gw_history: List[Dict], gw: int) -> Optional[int]:
    """Player's actual points in a GW (None if they didn't appear)."""
    rows = [h for h in gw_history if h.get("round") == gw]
    if not rows or all(h.get("minutes", 0) == 0 for h in rows):
        return None
    return sum(h.get("total_points", 0) for h in rows)  # DGW: both fixtures


def backtest_gameweek(archive: List[Dict], gw: int) -> Optional[Dict]:
    """Score the strategies for one gameweek. None if too few players appeared."""
    candidates = []
    for row in archive:
        history = row.get("gw_history") or []
        actual = actual_points_at(history, gw)
        if actual is None:
            continue
        stats = reconstruct_player_stats(history, gw)
        if stats is None:
            continue
        candidates.append({"name": row["player_name"], "actual": actual, **stats})

    if len(candidates) < 30:
        return None

    league_avg = round(sum(c["actual"] for c in candidates) / len(candidates), 2)

    def top_by(key: str) -> List[Dict]:
        return sorted(candidates, key=lambda c: c[key], reverse=True)[:TOP_N_STRATEGY]

    def avg_actual(bucket: List[Dict]) -> float:
        return round(sum(c["actual"] for c in bucket) / len(bucket), 2)

    ceiling_pick = top_by("ceiling_p90")[0]
    mean_pick = top_by("mean_pts")[0]
    best_possible = max(candidates, key=lambda c: c["actual"])

    return {
        "gameweek": gw,
        "players_scored": len(candidates),
        "league_avg_points": league_avg,
        "captain_by_ceiling": {"name": ceiling_pick["name"], "actual": ceiling_pick["actual"]},
        "captain_by_mean": {"name": mean_pick["name"], "actual": mean_pick["actual"]},
        "best_possible_captain": {"name": best_possible["name"], "actual": best_possible["actual"]},
        "hot_form_top10_avg": avg_actual(top_by("form_recent")),
        "consistency_top10_avg": avg_actual(top_by("consistency_score")),
        "ceiling_top10_avg": avg_actual(top_by("ceiling_p90")),
    }


def run_backtest(archive: List[Dict], start_gw: int, end_gw: int) -> Dict:
    """Backtest a GW range and aggregate strategy performance."""
    per_gw = []
    for gw in range(start_gw, end_gw + 1):
        result = backtest_gameweek(archive, gw)
        if result:
            per_gw.append(result)

    if not per_gw:
        return {"gameweeks": [], "summary": {"error": "no scoreable gameweeks in range"}}

    n = len(per_gw)
    ceiling_caps = [g["captain_by_ceiling"]["actual"] for g in per_gw]
    mean_caps = [g["captain_by_mean"]["actual"] for g in per_gw]
    best_caps = [g["best_possible_captain"]["actual"] for g in per_gw]
    league = [g["league_avg_points"] for g in per_gw]
    hot = [g["hot_form_top10_avg"] for g in per_gw]
    consistency = [g["consistency_top10_avg"] for g in per_gw]

    def avg(xs):
        return round(sum(xs) / len(xs), 2)

    summary = {
        "gameweeks_scored": n,
        "captaincy": {
            "by_ceiling_avg": avg(ceiling_caps),
            "by_mean_avg": avg(mean_caps),
            "best_possible_avg": avg(best_caps),
            "ceiling_beats_mean": sum(1 for c, m in zip(ceiling_caps, mean_caps) if c > m),
            "ties": sum(1 for c, m in zip(ceiling_caps, mean_caps) if c == m),
        },
        "form_signal": {
            "hot_top10_avg": avg(hot),
            "league_avg": avg(league),
            "edge_vs_league": round(avg(hot) - avg(league), 2),
        },
        "consistency_signal": {
            "consistency_top10_avg": avg(consistency),
            "edge_vs_league": round(avg(consistency) - avg(league), 2),
        },
    }
    return {"gameweeks": per_gw, "summary": summary}
