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

from agents.variability_agent import (
    CAPTAIN_BLEND,
    MIN_APPEARANCES,
    captaincy_score,
    compute_variability_stats,
    pick_captain_anchored,
)

logger = logging.getLogger(__name__)

TOP_N_STRATEGY = 10      # players per strategy bucket
MIN_PRIOR_APPEARANCES = MIN_APPEARANCES

# Re-exported for the weight-tuning scripts; CAPTAIN_BLEND lives in the agent.
__all__ = ["CAPTAIN_BLEND", "captaincy_score", "run_backtest", "backtest_gameweek"]


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
    # compute_variability_stats already includes form_recent (last-N appearances)
    return compute_variability_stats(points)


def actual_points_at(gw_history: List[Dict], gw: int) -> Optional[int]:
    """Player's actual points in a GW (None if they didn't appear)."""
    rows = [h for h in gw_history if h.get("round") == gw]
    if not rows or all(h.get("minutes", 0) == 0 for h in rows):
        return None
    return sum(h.get("total_points", 0) for h in rows)  # DGW: both fixtures


def reconstruct_market(gw_history: List[Dict], before_gw: int) -> Optional[Dict]:
    """
    Point-in-time market state a manager would have seen BEFORE before_gw
    (no lookahead). Drives the naive baselines:

    - season_pts: cumulative points so far -> "captain your best player"
    - price_at:   latest GW price (tenths of £m) -> "captain your most expensive"
    - owned_at:   latest GW ownership count -> "captain the template"

    price/ownership come from the element-summary history rows (`value`,
    `selected`) and may be absent in synthetic/partial archives -> None.
    """
    prior = sorted(
        (h for h in gw_history if h.get("round", 0) < before_gw),
        key=lambda h: h.get("round", 0),
    )
    if not prior:
        return None
    latest = prior[-1]
    return {
        "season_pts": sum(h.get("total_points", 0) for h in prior),
        "price_at": latest.get("value"),
        "owned_at": latest.get("selected"),
    }


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
        market = reconstruct_market(history, gw) or {}
        candidates.append({
            "name": row["player_name"], "actual": actual,
            "season_pts": market.get("season_pts", 0),
            "price_at": market.get("price_at"),
            "owned_at": market.get("owned_at"),
            **stats,
        })

    if len(candidates) < 30:
        return None

    league_avg = round(sum(c["actual"] for c in candidates) / len(candidates), 2)

    def top_by(key: str) -> List[Dict]:
        # None sinks to the bottom so absent price/ownership doesn't crash sorting
        return sorted(
            candidates,
            key=lambda c: (c.get(key) if c.get(key) is not None else float("-inf")),
            reverse=True,
        )[:TOP_N_STRATEGY]

    def avg_actual(bucket: List[Dict]) -> float:
        return round(sum(c["actual"] for c in bucket) / len(bucket), 2)

    def pick(key: str) -> Optional[Dict]:
        """Top-1 naive captain pick by `key`; None if no candidate has the data."""
        ranked = top_by(key)
        if not ranked or ranked[0].get(key) is None:
            return None
        return {"name": ranked[0]["name"], "actual": ranked[0]["actual"]}

    for c in candidates:
        c["blend"] = captaincy_score(c)

    ceiling_pick = top_by("ceiling_p90")[0]
    mean_pick = top_by("mean_pts")[0]
    blend_pick = top_by("blend")[0]
    # Production strategy: anchored on the season-points leader, deviating
    # to the blend pick only on a decisive blended-score edge.
    anchored_pick = pick_captain_anchored(candidates)
    best_possible = max(candidates, key=lambda c: c["actual"])

    return {
        "gameweek": gw,
        "players_scored": len(candidates),
        "league_avg_points": league_avg,
        "captain_by_ceiling": {"name": ceiling_pick["name"], "actual": ceiling_pick["actual"]},
        "captain_by_mean": {"name": mean_pick["name"], "actual": mean_pick["actual"]},
        "captain_by_blend": {"name": blend_pick["name"], "actual": blend_pick["actual"]},
        "captain_by_anchored": {"name": anchored_pick["name"], "actual": anchored_pick["actual"]},
        "best_possible_captain": {"name": best_possible["name"], "actual": best_possible["actual"]},
        # Naive manager baselines (no lookahead): the bar the smart heuristics must beat
        "captain_by_season_points": pick("season_pts"),   # "captain your best player so far"
        "captain_by_price": pick("price_at"),             # "captain your most expensive"
        "captain_by_ownership": pick("owned_at"),         # "captain the template"
        "hot_form_top10_avg": avg_actual(top_by("form_recent")),
        "consistency_top10_avg": avg_actual(top_by("consistency_score")),
        "ceiling_top10_avg": avg_actual(top_by("ceiling_p90")),
        # "Just pick the obvious best players" baseline for the form/consistency signals
        "naive_best10_avg": avg_actual(top_by("season_pts")),
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
    blend_caps = [g["captain_by_blend"]["actual"] for g in per_gw]
    anchored_caps = [g["captain_by_anchored"]["actual"] for g in per_gw]
    best_caps = [g["best_possible_captain"]["actual"] for g in per_gw]
    league = [g["league_avg_points"] for g in per_gw]
    hot = [g["hot_form_top10_avg"] for g in per_gw]
    consistency = [g["consistency_top10_avg"] for g in per_gw]
    naive_best = [g["naive_best10_avg"] for g in per_gw]

    def avg(xs):
        return round(sum(xs) / len(xs), 2)

    # Naive captain baselines, aligned per-GW with the smart picks. season_pts
    # is always available; price/ownership only when the archive carries them.
    def naive_series(key: str) -> Optional[List[int]]:
        vals = [g[key]["actual"] for g in per_gw if g.get(key)]
        return vals if len(vals) == n else None

    def head_to_head(smart: List[int], naive: Optional[List[int]]) -> Optional[Dict]:
        """Per-GW comparison: avg edge + win/loss/tie counts. Averages alone hide
        that one haul can carry a strategy, so we report how OFTEN it wins too."""
        if not naive:
            return None
        wins = sum(1 for s, b in zip(smart, naive) if s > b)
        losses = sum(1 for s, b in zip(smart, naive) if s < b)
        return {
            "smart_avg": avg(smart),
            "naive_avg": avg(naive),
            "avg_edge_per_gw": round(avg(smart) - avg(naive), 2),
            "smart_wins": wins,
            "naive_wins": losses,
            "ties": n - wins - losses,
        }

    season_caps = naive_series("captain_by_season_points")
    price_caps = naive_series("captain_by_price")
    owned_caps = naive_series("captain_by_ownership")

    # Primary bar = "captain your best player so far" (season points); the
    # toughest naive heuristic and always available. The production strategy
    # is the ANCHORED pick (baseline + decisive-deviation); the pure blend is
    # kept for reference.
    anchored_vs_best_player = head_to_head(anchored_caps, season_caps)
    anchored_vs_price = head_to_head(anchored_caps, price_caps)
    anchored_vs_template = head_to_head(anchored_caps, owned_caps)
    blend_vs_best_player = head_to_head(blend_caps, season_caps)
    blend_vs_price = head_to_head(blend_caps, price_caps)
    blend_vs_template = head_to_head(blend_caps, owned_caps)

    captaincy = {
        "by_ceiling_avg": avg(ceiling_caps),
        "by_mean_avg": avg(mean_caps),
        "by_blend_avg": avg(blend_caps),
        "by_anchored_avg": avg(anchored_caps),
        "best_possible_avg": avg(best_caps),
        "blend_beats_mean": sum(1 for b, m in zip(blend_caps, mean_caps) if b > m),
        "blend_beats_ceiling": sum(1 for b, c in zip(blend_caps, ceiling_caps) if b > c),
        # vs the naive baselines a real manager would use
        "naive_by_season_points_avg": avg(season_caps) if season_caps else None,
        "naive_by_price_avg": avg(price_caps) if price_caps else None,
        "naive_by_ownership_avg": avg(owned_caps) if owned_caps else None,
        # production strategy (anchored)
        "anchored_vs_best_player": anchored_vs_best_player,
        "anchored_vs_price": anchored_vs_price,
        "anchored_vs_template": anchored_vs_template,
        # reference: the unanchored blend (the old strategy)
        "blend_vs_best_player": blend_vs_best_player,
        "blend_vs_price": blend_vs_price,
        "blend_vs_template": blend_vs_template,
    }

    form_signal = {
        "hot_top10_avg": avg(hot),
        "league_avg": avg(league),
        "edge_vs_league": round(avg(hot) - avg(league), 2),
        # The honest bar: do hot-form picks beat just picking the best players?
        "naive_best10_avg": avg(naive_best),
        "edge_vs_naive_best": round(avg(hot) - avg(naive_best), 2),
    }
    consistency_signal = {
        "consistency_top10_avg": avg(consistency),
        "edge_vs_league": round(avg(consistency) - avg(league), 2),
        "naive_best10_avg": avg(naive_best),
        "edge_vs_naive_best": round(avg(consistency) - avg(naive_best), 2),
    }

    summary = {
        "gameweeks_scored": n,
        "captaincy": captaincy,
        "form_signal": form_signal,
        "consistency_signal": consistency_signal,
        "verdict": _build_verdict(
            n, anchored_vs_best_player, anchored_vs_template,
            form_signal, consistency_signal,
        ),
    }
    return {"gameweeks": per_gw, "summary": summary}


# Edge thresholds for the verdict: small enough to detect a real signal,
# large enough not to call sampling noise a "win". FPL captaincy swings are
# big, so ~0.4 pts/GW sustained over a season is a meaningful captaincy edge.
_CAPTAINCY_EDGE_PTS = 0.4
_SIGNAL_EDGE_PTS = 0.3


def _build_verdict(
    n: int,
    anchored_vs_best_player: Optional[Dict],
    anchored_vs_template: Optional[Dict],
    form_signal: Dict,
    consistency_signal: Dict,
) -> Dict:
    """
    Turn the numbers into an honest read of the PRODUCTION captaincy strategy
    (anchored: season-points leader unless the blend sees a decisively better
    pick). Conservative by design: a strategy only "beats" a baseline if it
    wins on BOTH average points AND the head-to-head win-rate, so a single
    lucky haul can't earn a pass.
    """
    notes: List[str] = []

    captaincy_beats_naive = False
    captaincy_matches_naive = False
    if anchored_vs_best_player:
        edge = anchored_vs_best_player["avg_edge_per_gw"]
        wins, losses = anchored_vs_best_player["smart_wins"], anchored_vs_best_player["naive_wins"]
        captaincy_beats_naive = edge >= _CAPTAINCY_EDGE_PTS and wins > losses
        captaincy_matches_naive = edge >= 0 and losses <= wins
        if captaincy_beats_naive:
            notes.append(
                f"Anchored captaincy beats 'captain your best player' by {edge} pts/GW "
                f"(won {wins}/{n}) — a real, if modest, edge."
            )
        elif captaincy_matches_naive:
            notes.append(
                f"Anchored captaincy matches the toughest naive baseline "
                f"(edge {edge:+} pts/GW, won {wins} lost {losses} of {n}) — by design it "
                f"defaults to that baseline and only deviates on decisive signal, so it "
                f"no longer underperforms it."
            )
        else:
            notes.append(
                f"Anchored captaincy UNDERPERFORMS the naive baseline here "
                f"(edge {edge} pts/GW, won {wins} lost {losses}). Raise the deviation "
                f"threshold (CAPTAIN_DEVIATION_THRESHOLD)."
            )

    captaincy_beats_template = False
    if anchored_vs_template:
        t_edge = anchored_vs_template["avg_edge_per_gw"]
        t_wins, t_losses = anchored_vs_template["smart_wins"], anchored_vs_template["naive_wins"]
        captaincy_beats_template = t_edge >= _CAPTAINCY_EDGE_PTS and t_wins > t_losses
        notes.append(
            f"Vs 'captain the template' (highest ownership): "
            f"{'BEATS it' if captaincy_beats_template else 'no clear edge'} "
            f"({t_edge:+} pts/GW, won {t_wins} lost {t_losses} of {n})."
        )

    form_real = form_signal["edge_vs_naive_best"] >= _SIGNAL_EDGE_PTS
    notes.append(
        f"Hot-form picks {'beat' if form_real else 'do NOT beat'} just picking the "
        f"best players ({form_signal['edge_vs_naive_best']:+} pts/GW vs naive top-10). "
        + ("" if form_real else "Treat form as context for the LLM, not as a selection signal.")
    )

    consistency_real = consistency_signal["edge_vs_naive_best"] >= _SIGNAL_EDGE_PTS
    notes.append(
        f"Consistency core {'beats' if consistency_real else 'does NOT beat'} the naive "
        f"top-10 ({consistency_signal['edge_vs_naive_best']:+} pts/GW)."
        + ("" if consistency_real else " Use consistency for bench/floor decisions only.")
    )

    any_edge = captaincy_beats_naive or captaincy_beats_template or form_real or consistency_real
    if captaincy_beats_naive:
        bottom = "Bottom line: the deterministic core shows measurable edge over naive play."
    elif captaincy_matches_naive and captaincy_beats_template:
        bottom = (
            "Bottom line: captaincy now matches the best naive baseline (it can no longer "
            "lose to it by construction) and beats template-captaincy. Real alpha beyond "
            "that must come from the unmeasured layers: odds, news and the LLM."
        )
    else:
        bottom = (
            "Bottom line: on this season the deterministic core does not clearly beat naive "
            "play. Treat Hermes as decision-support/explanation, not as alpha — and re-run "
            "across more seasons before trusting it."
        )
    notes.append(bottom)

    return {
        "captaincy_beats_naive": captaincy_beats_naive,
        "captaincy_matches_naive": captaincy_matches_naive,
        "captaincy_beats_template": captaincy_beats_template,
        "form_signal_real": form_real,
        "consistency_signal_real": consistency_real,
        "has_measurable_edge": any_edge,
        "caveat": (
            "Single-season, deterministic core only (no betting/news/LLM, no ownership "
            "for differential value). Small samples — read win-rates, not just averages."
        ),
        "notes": notes,
    }
