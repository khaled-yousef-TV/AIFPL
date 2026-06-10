"""
Post-gameweek evaluation of Hermes runs (pure functions — no I/O).

Scores what Hermes recommended against what actually happened, producing
the evaluation JSON stored on HermesRun and the calibration profile that
feeds future prompts and the trust weight.
"""

from typing import Dict, List, Optional

# Trust weight bounds: even a badly calibrated Hermes keeps a little
# influence (floor), a perfect one never exceeds full effect (ceiling).
TRUST_MIN = 0.3
TRUST_MAX = 1.0
# A boost "hits" if the player scored at least this many points
BOOST_HIT_POINTS = 5
# A fade/exclude "hits" if the player scored at most this many points
FADE_HIT_POINTS = 4


def evaluate_run(
    adjustments: Optional[Dict],
    result: Optional[Dict],
    actual_points: Dict[int, int],
    signals: Optional[Dict] = None,
) -> Dict:
    """
    Score one Hermes run against actual GW points.

    Args:
        adjustments: stored HermesAdjustments dump (may be None for degraded runs)
        result: stored optimizer result dict
        actual_points: {player_id: actual_gw_points}
        signals: stored agent reports (for per-agent calibration)

    Returns evaluation dict (JSON-serializable).
    """
    evaluation: Dict = {"scored_players": len(actual_points)}

    if adjustments:
        evaluation["adjustments"] = _score_adjustments(adjustments.get("adjustments", []), actual_points)
        evaluation["captaincy"] = _score_captaincy(adjustments.get("captain_ranking", []), actual_points)
        evaluation["transfers"] = _score_transfers(adjustments.get("transfer_priorities", []), actual_points)
        evaluation["differentials"] = _score_differentials(adjustments.get("differentials", []), actual_points)

    if result and result.get("squad"):
        evaluation["squad"] = _score_squad(result["squad"], actual_points)

    if signals:
        evaluation["agents"] = _score_agents(signals, actual_points)

    return evaluation


def _score_adjustments(adjustments: List[Dict], actual: Dict[int, int]) -> Dict:
    """Hit-rate per action type."""
    outcome = {a: {"hits": 0, "total": 0, "details": []} for a in ("boost", "fade", "exclude", "lock")}
    for adj in adjustments:
        pid = adj.get("player_id")
        action = adj.get("action", "boost")
        if pid not in actual or action not in outcome:
            continue
        pts = actual[pid]
        hit = pts >= BOOST_HIT_POINTS if action in ("boost", "lock") else pts <= FADE_HIT_POINTS
        outcome[action]["total"] += 1
        outcome[action]["hits"] += int(hit)
        outcome[action]["details"].append({"player_id": pid, "points": pts, "hit": hit})

    return {
        action: {
            "hits": data["hits"],
            "total": data["total"],
            "hit_rate": round(data["hits"] / data["total"], 3) if data["total"] else None,
            "details": data["details"][:10],
        }
        for action, data in outcome.items()
        if data["total"] > 0
    }


def _score_captaincy(captain_ranking: List[int], actual: Dict[int, int]) -> Optional[Dict]:
    """Regret: best candidate's points minus the #1 pick's points."""
    ranked = [pid for pid in captain_ranking if pid in actual]
    if not ranked:
        return None
    picked = ranked[0]
    picked_pts = actual[picked]
    best = max(ranked, key=lambda pid: actual[pid])
    return {
        "picked_id": picked,
        "picked_points": picked_pts,
        "best_id": best,
        "best_points": actual[best],
        "regret": actual[best] - picked_pts,  # 0 = perfect pick
        "candidates_scored": len(ranked),
    }


def _score_transfers(transfers: List[Dict], actual: Dict[int, int]) -> List[Dict]:
    """Realized delta of each suggested (out -> in) pair."""
    scored = []
    for t in transfers:
        out_id, in_id = t.get("out_id"), t.get("in_id")
        if out_id in actual and in_id in actual:
            scored.append({
                "out_id": out_id, "in_id": in_id,
                "delta": actual[in_id] - actual[out_id],
                "urgency": t.get("urgency"),
            })
    return scored


def _score_differentials(differentials: List[int], actual: Dict[int, int]) -> Optional[Dict]:
    scored = [actual[pid] for pid in differentials if pid in actual]
    if not scored:
        return None
    return {
        "count": len(scored),
        "avg_points": round(sum(scored) / len(scored), 2),
        "returns": sum(1 for p in scored if p >= BOOST_HIT_POINTS),
    }


def _score_squad(squad: Dict, actual: Dict[int, int]) -> Optional[Dict]:
    """Actual points of the recommended XI (captain doubled), vs projection."""
    xi = squad.get("starting_xi", [])
    if not xi:
        return None
    total = 0
    covered = 0
    for p in xi:
        pid = p.get("id")
        if pid in actual:
            pts = actual[pid]
            if p.get("is_captain"):
                pts *= 2
            total += pts
            covered += 1
    return {
        "actual_points": total,
        "projected_points": squad.get("predicted_points"),
        "players_scored": covered,
    }


def _score_agents(signals: Dict, actual: Dict[int, int]) -> Dict:
    """Per-agent calibration checks."""
    out: Dict = {}

    # Availability: did flagged players indeed not play / blank?
    avail = (signals.get("availability") or {}).get("payload", {})
    flags = avail.get("flagged", [])
    if flags:
        checked = [f for f in flags if f.get("id") in actual]
        correct = sum(1 for f in checked if actual[f["id"]] <= 2)
        if checked:
            out["availability_flag_accuracy"] = round(correct / len(checked), 3)

    # Variability: did the p10..p90 band bracket the actual score?
    var = (signals.get("variability") or {}).get("payload", {})
    entries = var.get("players", [])
    if entries:
        bracketed = 0
        checked = 0
        for e in entries:
            pid = e.get("id")
            if pid in actual:
                checked += 1
                if e.get("floor_p10", 0) <= actual[pid] <= e.get("ceiling_p90", 99):
                    bracketed += 1
        if checked:
            out["variability_band_coverage"] = round(bracketed / checked, 3)

    # Form: did hot players outscore cold players on average?
    form = (signals.get("form") or {}).get("payload", {})
    hot = [actual[e["id"]] for e in form.get("hot_players", []) if e.get("id") in actual]
    cold = [actual[e["id"]] for e in form.get("cold_players", []) if e.get("id") in actual]
    if hot and cold:
        out["form_hot_avg"] = round(sum(hot) / len(hot), 2)
        out["form_cold_avg"] = round(sum(cold) / len(cold), 2)

    return out


# ==================== Calibration across runs ====================

def build_calibration_profile(evaluations: List[Dict]) -> Dict:
    """
    Aggregate evaluations (most-recent-first) into a calibration profile.

    Returns {action_hit_rates, captain_regret_avg, trust_weights, runs_scored}.
    """
    action_totals: Dict[str, List[int]] = {}
    regrets: List[int] = []

    for ev in evaluations:
        for action, data in (ev.get("adjustments") or {}).items():
            bucket = action_totals.setdefault(action, [0, 0])
            bucket[0] += data.get("hits", 0)
            bucket[1] += data.get("total", 0)
        cap = ev.get("captaincy")
        if cap and cap.get("regret") is not None:
            regrets.append(cap["regret"])

    hit_rates = {
        action: round(hits / total, 3)
        for action, (hits, total) in action_totals.items()
        if total > 0
    }

    return {
        "runs_scored": len(evaluations),
        "action_hit_rates": hit_rates,
        "action_samples": {a: t for a, (h, t) in action_totals.items()},
        "captain_regret_avg": round(sum(regrets) / len(regrets), 2) if regrets else None,
        "trust_weights": {a: compute_trust(r) for a, r in hit_rates.items()},
    }


def compute_trust(hit_rate: Optional[float]) -> float:
    """
    Map a hit-rate to a trust weight in [TRUST_MIN, TRUST_MAX].

    0.5 hit-rate (coin flip) -> mid trust; 1.0 -> full trust; 0.0 -> floor.
    """
    if hit_rate is None:
        return 1.0  # no data: don't dampen
    trust = TRUST_MIN + (TRUST_MAX - TRUST_MIN) * hit_rate
    return round(min(TRUST_MAX, max(TRUST_MIN, trust)), 3)


def apply_trust(multiplier: float, trust: float) -> float:
    """Shrink a multiplier toward 1.0 by trust: 1 + (m - 1) * trust."""
    return round(1.0 + (multiplier - 1.0) * trust, 4)


def calibration_digest(profile: Dict, lessons: List[Dict]) -> str:
    """Compact text digest of calibration + lessons for the Hermes prompt."""
    if not profile.get("runs_scored") and not lessons:
        return ""

    lines = []
    if profile.get("runs_scored"):
        lines.append(f"Scored runs: {profile['runs_scored']}.")
        for action, rate in (profile.get("action_hit_rates") or {}).items():
            samples = profile.get("action_samples", {}).get(action, 0)
            lines.append(f"- Your '{action}' calls hit {rate:.0%} ({samples} samples).")
        if profile.get("captain_regret_avg") is not None:
            lines.append(f"- Avg captaincy regret: {profile['captain_regret_avg']} pts vs best candidate.")
    if lessons:
        lines.append("Lessons from past gameweeks:")
        lines += [f"- [{l['category']}] {l['lesson']}" for l in lessons]
    return "\n".join(lines)
