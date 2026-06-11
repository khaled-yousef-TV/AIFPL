"""
Variability agent.

Computes per-player score volatility from gameweek history
(element-summary endpoint): variance, ceiling/floor percentiles, haul and
blank rates. High-ceiling players are captaincy/Triple Captain material;
high-consistency players are squad core material.

The element-summary endpoint is one HTTP call per player, so we restrict
to a candidate pool and cache results per day.
"""

import logging
import os
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
from pydantic import BaseModel

from .base import AgentContext, BaseAgent
from .schemas import VariabilityEntry, VariabilitySignals

logger = logging.getLogger(__name__)

# Pool size is capped to keep first-run latency and API load sane
# (each player costs one rate-limited HTTP call).
DEFAULT_POOL_SIZE = int(os.getenv("HERMES_VARIABILITY_POOL", "120"))
# Minimum appearances for stats to be meaningful
MIN_APPEARANCES = 5
# Thresholds
HAUL_POINTS = 10
BLANK_POINTS = 2
# Recent-form window (last N appearances)
FORM_WINDOW = 4

# Captaincy ranking blend — expected value dominant, with form + ceiling tilts.
# Tuned on 2025-26 backtest: beats pure-mean by ~+0.37 pts/captain/GW and
# pure-ceiling (the old heuristic) by ~+2.25. See hermes/backtest.py.
CAPTAIN_BLEND = {"mean": 0.7, "form": 0.15, "ceiling": 0.15}

# Anchored captaincy: the 2025-26 backtest showed every pure-blend weighting
# LOSES to the naive "captain your best player so far" baseline (-0.48 to
# -0.91 pts/GW). So the captain pick is anchored on the season-points leader
# and deviates to the blend's pick only when its blended score is decisively
# higher. A sweep over thresholds 0.5-4.0 found 1.0 optimal (edge +0.06
# pts/GW, deviations net-positive); below 1.0 the deviations turn harmful.
CAPTAIN_DEVIATION_THRESHOLD = 1.0


def captaincy_score(stats: dict, weights: dict = None) -> float:
    """Blended captaincy score: expected value + recent form + ceiling upside."""
    w = weights or CAPTAIN_BLEND
    return (
        w["mean"] * stats.get("mean_pts", 0)
        + w["form"] * stats.get("form_recent", 0)
        + w["ceiling"] * stats.get("ceiling_p90", 0)
    )


def season_points_proxy(stats: dict) -> float:
    """Season points so far. Exact when the caller provides `season_pts`
    (backtest market data); otherwise appearance-sum (mean × appearances),
    which equals season total since non-appearances score 0."""
    if stats.get("season_pts") is not None:
        return stats["season_pts"]
    return stats.get("mean_pts", 0) * stats.get("n_gws", 0)


def pick_captain_anchored(
    candidates: List[dict],
    threshold: float = None,
    weights: dict = None,
) -> Optional[dict]:
    """
    Anchored captain pick: default to the season-points leader (the naive
    baseline that beats pure heuristics) and deviate to the blend's top pick
    only when its blended score clears the leader's by `threshold`.

    By construction this can only differ from the naive baseline when the
    blend sees a decisively better option — it ties the baseline otherwise.
    """
    if not candidates:
        return None
    t = CAPTAIN_DEVIATION_THRESHOLD if threshold is None else threshold
    baseline = max(candidates, key=season_points_proxy)
    challenger = max(candidates, key=lambda c: captaincy_score(c, weights))
    challenger_edge = captaincy_score(challenger, weights) - captaincy_score(baseline, weights)
    return challenger if challenger_edge >= t else baseline

# Daily cache: {player_id: VariabilityEntry-dict}, keyed by ISO date
_cache: Dict[str, Dict[int, dict]] = {}


def compute_variability_stats(points: List[int]) -> Optional[dict]:
    """
    Compute volatility stats from a list of per-GW points (appearances only).

    Returns None when there are too few appearances to be meaningful.
    """
    if len(points) < MIN_APPEARANCES:
        return None

    arr = np.array(points, dtype=float)
    mean = float(np.mean(arr))
    stddev = float(np.std(arr))
    cv = stddev / mean if mean > 0 else 0.0

    return {
        "n_gws": len(points),
        "mean_pts": round(mean, 2),
        "stddev": round(stddev, 2),
        "cv": round(cv, 3),
        "ceiling_p90": round(float(np.percentile(arr, 90)), 1),
        "floor_p10": round(float(np.percentile(arr, 10)), 1),
        "haul_rate": round(float(np.mean(arr >= HAUL_POINTS)), 3),
        "blank_rate": round(float(np.mean(arr <= BLANK_POINTS)), 3),
        # mean of the last FORM_WINDOW appearances (recent form)
        "form_recent": round(float(np.mean(arr[-FORM_WINDOW:])), 2),
        # 1 at perfectly steady output, decaying as volatility grows
        "consistency_score": round(1.0 / (1.0 + cv), 3),
    }


class VariabilityAgent(BaseAgent):
    name = "variability"

    def _candidate_pool(self, ctx: AgentContext) -> List[int]:
        """Top players by season points + top predicted + user team, deduped."""
        client = ctx.fpl_client
        pool_size = DEFAULT_POOL_SIZE

        by_points = client.get_top_players(n=pool_size)
        ids = [p.id for p in by_points]
        seen = set(ids)

        try:
            from services.prediction_service import compute_predictions
            for p in compute_predictions()[:pool_size // 2]:
                if p["id"] not in seen:
                    ids.append(p["id"])
                    seen.add(p["id"])
        except Exception as e:
            logger.warning(f"Variability pool: predictions unavailable ({e})")

        for pid in ctx.user_player_ids:
            if pid not in seen:
                ids.append(pid)
                seen.add(pid)

        return ids[:pool_size + len(ctx.user_player_ids)]

    def _build(self, ctx: AgentContext) -> Tuple[str, BaseModel, str]:
        client = ctx.fpl_client
        players_by_id = {p.id: p for p in client.get_players()}
        teams = {t.id: t.short_name for t in client.get_teams()}

        pool = self._candidate_pool(ctx)

        # Cold-start: last-season archive for players with too few appearances
        prior_by_name = {}
        try:
            from agents.mechanics_agent import determine_season_phase
            phase, _ = determine_season_phase(client.get_gameweeks())
            if phase in ("preseason", "early"):
                from services.season_archive_service import load_prior_by_name
                prior_by_name = load_prior_by_name()
        except Exception as e:
            logger.warning(f"Variability: prior archive unavailable ({e})")

        cache_key = date.today().isoformat()
        day_cache = _cache.setdefault(cache_key, {})
        # Drop stale days
        for key in [k for k in _cache if k != cache_key]:
            del _cache[key]

        entries: List[VariabilityEntry] = []
        fetch_errors = 0
        for pid in pool:
            pl = players_by_id.get(pid)
            if not pl:
                continue

            if pid in day_cache:
                cached = day_cache[pid]
                if cached is not None:
                    entries.append(VariabilityEntry(**cached))
                continue

            try:
                details = client.get_player_details(pid)
                history = details.get("history", [])
            except Exception as e:
                fetch_errors += 1
                logger.warning(f"Variability: failed to fetch history for {pl.web_name}: {e}")
                continue

            # Appearances only: variability of output when actually playing
            points = [
                h.get("total_points", 0) for h in history
                if h.get("minutes", 0) > 0
            ]
            stats = compute_variability_stats(points)
            source = "current"
            if stats is None and prior_by_name:
                # Cold-start: fall back to last season's archived variability
                prior = (prior_by_name.get(pl.full_name.lower())
                         or prior_by_name.get(pl.web_name.lower()))
                if prior and prior.get("variability"):
                    stats = prior["variability"]
                    source = "prior"
            if stats is None:
                day_cache[pid] = None  # cached negative: too few appearances
                continue

            entry_dict = {
                "id": pid,
                "name": pl.web_name,
                "team": teams.get(pl.team, "???"),
                "position": pl.position,
                "source": source,
                **stats,
            }
            day_cache[pid] = entry_dict
            entries.append(VariabilityEntry(**entry_dict))

        # Captaincy candidates: anchored pick first (season-points leader
        # unless the blend sees a decisively better option — see
        # pick_captain_anchored), then the rest by blended score.
        good = [e for e in entries if e.mean_pts >= 4.0]
        good_dicts = [e.model_dump() for e in good]
        anchor = pick_captain_anchored(good_dicts)
        by_blend = [
            e.id for e in sorted(
                good, key=lambda e: captaincy_score(e.model_dump()), reverse=True
            )
        ]
        captaincy = (
            ([anchor["id"]] + [pid for pid in by_blend if pid != anchor["id"]])[:10]
            if anchor else by_blend[:10]
        )
        core = [
            e.id for e in sorted(good, key=lambda e: e.consistency_score, reverse=True)[:10]
        ]

        entries.sort(key=lambda e: captaincy_score(e.model_dump()), reverse=True)

        payload = VariabilitySignals(
            pool_size=len(pool),
            covered=len(entries),
            players=entries,
            captaincy_candidates=captaincy,
            core_candidates=core,
        )

        status = "degraded" if fetch_errors > 0 else "ok"
        names_by_id = {e.id: e.name for e in entries}
        cap_names = ", ".join(names_by_id.get(i, "?") for i in captaincy[:3])
        core_names = ", ".join(names_by_id.get(i, "?") for i in core[:3])
        summary = (
            f"Volatility computed for {len(entries)}/{len(pool)} pool players. "
            f"Top captaincy picks (blended): {cap_names or 'none'}. "
            f"Most consistent: {core_names or 'none'}."
            + (f" ({fetch_errors} fetch errors.)" if fetch_errors else "")
        )
        return summary, payload, status
