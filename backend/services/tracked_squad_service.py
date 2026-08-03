"""
Tracked-squad service.

A persistent 15-man squad that Hermes drives week to week, meant as a pure
benchmark of "what would Hermes do if it played the whole season". The
service is a strict executor of Hermes's decisions — it must NEVER invent
a move Hermes didn't recommend. If the run is degraded (no LLM) or the
recommended transfer is illegal (bank, 3-per-club, positional shape), the
correct behaviour is to hold and record why in `transfers_made`, so the
ledger reads "we intended to move but couldn't" rather than presenting an
optimizer pick as Hermes advice.
"""

import logging
import math
import time
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from .dependencies import get_dependencies

logger = logging.getLogger(__name__)

MAX_FREE_TRANSFERS = 2
HIT_COST_PER_EXTRA_TRANSFER = 4
POSITIONAL_SHAPE = {1: 2, 2: 5, 3: 5, 4: 3}   # GK / DEF / MID / FWD
MAX_PER_CLUB = 3
SQUAD_SIZE = 15


# ==================== Seed ====================

def seed_from_best_squad() -> Dict[str, Any]:
    """
    Seed the tracked squad from the latest completed `squad` (Best Squad)
    Hermes run. Idempotent: refuses if a GW1+ row already exists so a
    second click can't overwrite an in-flight benchmark.
    """
    deps = get_dependencies()
    db = deps.db_manager

    existing = db.get_tracked_squad_state()
    if existing:
        raise ValueError(
            f"Tracked squad already seeded (GW{existing['gameweek']}). "
            "Reset via /api/tracked-squad/reset before re-seeding."
        )

    run = db.get_latest_hermes_run(run_type="squad", statuses=["completed"])
    if not run or not run.get("result") or not run["result"].get("squad"):
        raise ValueError(
            "No completed Best Squad run available to seed from. Run a "
            "`squad` Hermes run first."
        )

    squad = run["result"]["squad"]
    xi = squad.get("starting_xi") or []
    bench = squad.get("bench") or []
    if len(xi) + len(bench) != SQUAD_SIZE:
        raise ValueError(
            f"Best Squad result has {len(xi) + len(bench)} players, "
            f"expected {SQUAD_SIZE}"
        )

    ordered = _order_for_autosubs(xi, bench)
    prices = {p["id"]: p["price"] for p in xi + bench}
    captain = squad.get("captain") or {}
    vice = squad.get("vice_captain") or {}

    # FPL's start-of-season bank: £100m — squad cost = leftover.
    starting_budget = 100.0
    total_cost = sum(prices.values())
    bank = round(starting_budget - total_cost, 1)

    next_gw = deps.fpl_client.get_next_gameweek()
    # First tracked row is the squad entering GW1 (or the next unplayed GW
    # if seeding mid-season — supported so a benchmark can start any week).
    seed_gw = next_gw.id if next_gw else 1

    db.save_tracked_squad_state(
        gameweek=seed_gw,
        players=[p["id"] for p in ordered],
        purchase_prices=prices,
        captain_id=captain.get("id") or ordered[0]["id"],
        vice_id=vice.get("id"),
        bank=bank,
        free_transfers=1,
        chips_used=[],
        chip_active=None,
    )
    logger.info(
        f"Tracked squad seeded for GW{seed_gw} from run {run['run_id']} "
        f"({len(ordered)} players, £{total_cost:.1f}m spent, £{bank:.1f}m bank)"
    )
    return db.get_tracked_squad_state()


def _order_for_autosubs(xi: List[Dict], bench: List[Dict]) -> List[Dict]:
    """
    Return the 15 in FPL pick order: starting XI GK→FWD, then bench in
    sub-order (outfield subs first by predicted rank, GK last). Autosub
    scoring depends on that ordering (subs are attempted in that list
    order when a starter blanks).
    """
    xi_ordered = sorted(xi, key=lambda p: (p["position_id"], -p["predicted"]))
    bench_gk = [p for p in bench if p["position_id"] == 1]
    bench_outfield = sorted(
        (p for p in bench if p["position_id"] != 1),
        key=lambda p: -p["predicted"],
    )
    return xi_ordered + bench_outfield + bench_gk


# ==================== Current state ====================

def current_state() -> Optional[Dict[str, Any]]:
    """
    Latest tracked-squad state row + ledger + name lookup + a squad-shaped
    payload matching `assemble_squad_result`, so the tracked view can reuse
    the same PitchView UI as Best Squad.
    """
    deps = get_dependencies()
    db = deps.db_manager
    latest = db.get_tracked_squad_state()
    if not latest:
        return None
    return {
        "state": latest,
        "history": db.get_tracked_squad_states(),
        "ledger": db.get_tracked_squad_gws(),
        "players": _player_lookup_for(latest["players"], deps.fpl_client),
        "squad": _build_squad_payload(latest, deps.fpl_client),
    }


# compute_player_predictions is O(all players) and takes 20+s cold. The
# tracked squad only changes when apply_transfer runs (once per GW), so a
# short-TTL cache is enough to keep the GET endpoint snappy while still
# picking up fresh predictions the next time an underlying value changes.
_PRED_CACHE: Dict[str, Any] = {"data": None, "at": 0.0}
_PRED_CACHE_TTL_SECONDS = 300  # 5 minutes


def _cached_predictions() -> Dict[int, Dict]:
    now = time.time()
    if _PRED_CACHE["data"] is not None and now - _PRED_CACHE["at"] < _PRED_CACHE_TTL_SECONDS:
        return _PRED_CACHE["data"]
    from services.squad_service import compute_player_predictions
    try:
        preds = {p["id"]: p for p in compute_player_predictions(_get_predictor())}
    except Exception as e:
        logger.warning(f"Tracked squad: predictions unavailable ({e}); rendering without them.")
        preds = {}
    _PRED_CACHE["data"] = preds
    _PRED_CACHE["at"] = now
    return preds


def _build_squad_payload(state: Dict, fpl) -> Dict[str, Any]:
    """
    Build a squad-shaped dict (starting_xi/bench/captain/vice/formation) that
    matches the frontend PitchView contract used by every other view.

    Uses the stored FPL pick order (first 11 = XI, last 4 = bench) rather
    than re-running the optimizer — this is the *actual* squad Hermes chose
    to play, not a re-optimization of it. Predicted-points come from the
    heuristic predictor so the pitch looks the same as Best Squad.
    """
    picks = list(state["players"])
    captain_id = state["captain_id"]
    vice_id = state.get("vice_id")
    predictions = _cached_predictions()

    teams = {t.id: t.short_name for t in fpl.get_teams()}
    position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    all_players = {p.id: p for p in fpl.get_players()}

    def pdict(pid: int) -> Dict[str, Any]:
        pred = predictions.get(pid) or {}
        p = all_players.get(pid)
        return {
            "id": pid,
            "name": (pred.get("name") or getattr(p, "web_name", f"#{pid}")),
            "team": pred.get("team") or teams.get(getattr(p, "team", 0), "?"),
            "team_id": pred.get("team_id") or getattr(p, "team", 0),
            "position": pred.get("position") or position_map.get(getattr(p, "element_type", 0), "?"),
            "position_id": pred.get("position_id") or getattr(p, "element_type", 0),
            "price": pred.get("price") if pred.get("price") is not None else getattr(p, "price", 0),
            "predicted": pred.get("predicted") if pred.get("predicted") is not None else 0.0,
            "opponent": pred.get("opponent"),
            "difficulty": pred.get("difficulty"),
            "is_home": pred.get("is_home"),
            "is_captain": pid == captain_id,
            "is_vice_captain": pid == vice_id,
        }

    xi = [pdict(pid) for pid in picks[:11]]
    bench = [pdict(pid) for pid in picks[11:]]

    # Formation string from XI position counts
    from collections import Counter
    pos_counts = Counter(p["position"] for p in xi)
    formation = f"{pos_counts.get('DEF', 0)}-{pos_counts.get('MID', 0)}-{pos_counts.get('FWD', 0)}"

    total_cost = round(sum((p["price"] or 0) for p in xi + bench), 1)
    predicted_points = round(
        sum(p["predicted"] for p in xi)
        + next((p["predicted"] for p in xi if p["is_captain"]), 0),
        1,
    )

    captain = next((p for p in xi if p["is_captain"]), xi[0] if xi else None)
    vice = next((p for p in xi if p["is_vice_captain"]), None)

    return {
        "method": "tracked",
        "formation": formation,
        "starting_xi": xi,
        "bench": bench,
        "captain": {"id": captain["id"], "name": captain["name"], "predicted": round(captain["predicted"], 2)} if captain else None,
        "vice_captain": {"id": vice["id"], "name": vice["name"], "predicted": round(vice["predicted"], 2)} if vice else None,
        "total_cost": total_cost,
        "remaining_budget": round(state.get("bank", 0.0), 1),
        "predicted_points": predicted_points,
        "is_fixed_squad": True,
    }


def _get_predictor():
    """Best-effort heuristic predictor lookup (kept lazy for testability)."""
    from services.dependencies import get_dependencies
    return get_dependencies().predictor_heuristic


def _player_lookup_for(player_ids: List[int], fpl) -> Dict[str, Dict[str, Any]]:
    """{id (as str): {name, team, position, price, status}} for the given ids.

    Str keys because JSON round-trips make int keys inconsistent across
    SQLite/Postgres and the ledger already uses str-keyed purchase_prices.
    """
    position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    all_players = {p.id: p for p in fpl.get_players()}
    teams = {t.id: t.short_name for t in fpl.get_teams()}
    out: Dict[str, Dict[str, Any]] = {}
    for pid in player_ids:
        p = all_players.get(pid)
        if p is None:
            out[str(pid)] = {"name": f"#{pid}", "team": "?", "position": "?", "price": None, "status": "?"}
            continue
        out[str(pid)] = {
            "name": getattr(p, "web_name", f"#{pid}"),
            "team": teams.get(p.team, "?"),
            "position": position_map.get(p.element_type, "?"),
            "price": getattr(p, "price", None),
            "status": getattr(p, "status", "a"),
        }
    return out


# ==================== Auto-apply transfer ====================

def apply_transfer(hermes_run: Dict[str, Any]) -> Dict[str, Any]:
    """
    Advance the tracked squad by one gameweek using this Hermes run.

    Reads the latest state (the squad that entered GW_N — the deadline the
    run was prepared FOR), then writes the state for GW_{N+1} reflecting
    the recommended transfers (or a hold). Also writes a ledger row on GW_N
    recording what actually happened (transfers made, hit cost, hermes_run_id).

    Never invents a move Hermes didn't emit. Degraded runs and illegal
    recommendations both resolve to "hold with reason".
    """
    deps = get_dependencies()
    db = deps.db_manager
    fpl = deps.fpl_client

    current = db.get_tracked_squad_state()
    if not current:
        raise ValueError("No tracked-squad state — seed via /api/tracked-squad/seed first.")

    gw_from = current["gameweek"]
    gw_to = gw_from + 1

    # If we've already written the next row (idempotent scheduler retries or
    # a manual second click), don't double-apply.
    if db.get_tracked_squad_state(gameweek=gw_to):
        logger.info(f"Tracked squad already advanced to GW{gw_to}; skipping apply.")
        return current_state()

    live_prices = {p.id: p.price for p in fpl.get_players()}
    result = (hermes_run or {}).get("result") or {}
    adjustments = (hermes_run or {}).get("adjustments") or {}
    plan = result.get("transfer_plan") or {}
    priorities = result.get("transfer_priorities") or []

    # Move set: only "this_week" priorities are executed. A `hold` verdict
    # forces the list empty regardless of priorities content — the two must
    # agree (validation.py enforces the reverse; be defensive here too).
    is_degraded = (hermes_run or {}).get("status") == "degraded"
    is_hold = plan.get("recommendation") == "hold"

    moves: List[Dict[str, Any]] = []
    hold_reason: Optional[str] = None

    if is_degraded:
        hold_reason = (
            "Hermes LLM unavailable this deadline — deterministic signals "
            "only, so no transfer was recommended. Rolling the free transfer."
        )
    elif is_hold:
        hold_reason = plan.get("reason") or "Hermes recommended holding the transfer."
    else:
        moves = [
            {"out_id": t["out_id"], "in_id": t["in_id"], "reason": t.get("reason", "")}
            for t in priorities if t.get("urgency") == "this_week"
        ]
        if not moves:
            # `transfer` verdict with no this_week moves — treat as hold rather
            # than fabricate. Validation should have coerced this already.
            hold_reason = (
                "Hermes verdict was 'transfer' but no move had urgency=='this_week'. "
                "Falling back to hold to avoid inventing a decision."
            )

    # Try to apply the moves; on illegality, hold with a recorded reason.
    if moves and hold_reason is None:
        try:
            new_players, new_prices, new_bank = _execute_moves(
                current, moves, live_prices, fpl,
            )
        except _IllegalMove as e:
            hold_reason = f"Recommended transfer was illegal: {e}. Held instead."
            moves = []

    # Captain: first ranked candidate who's in the (post-move) squad AND not
    # availability-flagged this week. Never a heuristic pick.
    availability = ((hermes_run or {}).get("signals") or {}).get("availability") or {}
    flagged_ids: Set[int] = {
        f["id"] if isinstance(f, dict) else f
        for f in (availability.get("payload", {}).get("flagged") or [])
    }

    post_move_squad = new_players if moves else current["players"]
    captain_ranking = [
        c["id"] if isinstance(c, dict) else c
        for c in (result.get("captain_ranking") or [])
    ]
    captain_id = _pick_captain(post_move_squad, captain_ranking, flagged_ids, current["captain_id"])
    vice_id = _pick_vice(post_move_squad, captain_ranking, flagged_ids, captain_id)

    # Ledger row for GW_N — this is what happened as we entered it.
    hits = max(0, len(moves) - current["free_transfers"])
    transfer_cost = hits * HIT_COST_PER_EXTRA_TRANSFER
    ledger_transfers = list(moves)
    if hold_reason:
        # Record intent even when nothing moved, so the ledger explains itself.
        ledger_transfers.append({"held": True, "reason": hold_reason})

    db.save_tracked_squad_gw(
        gameweek=gw_from,
        transfer_cost=transfer_cost,
        transfers_made=ledger_transfers,
        hermes_run_id=(hermes_run or {}).get("run_id"),
    )

    # State row for GW_{N+1}.
    if moves:
        next_players = new_players
        next_prices = new_prices
        next_bank = new_bank
        next_ft = max(1, min(MAX_FREE_TRANSFERS,
                             (current["free_transfers"] - len(moves)) + 1))
    else:
        next_players = list(current["players"])
        next_prices = dict(current["purchase_prices"])
        next_bank = current["bank"]
        next_ft = min(MAX_FREE_TRANSFERS, current["free_transfers"] + 1)

    db.save_tracked_squad_state(
        gameweek=gw_to,
        players=next_players,
        purchase_prices=next_prices,
        captain_id=captain_id,
        vice_id=vice_id,
        bank=round(next_bank, 1),
        free_transfers=next_ft,
        chips_used=current["chips_used"],
        chip_active=None,   # chips are recorded on the GW they're played
    )
    logger.info(
        f"Tracked squad advanced GW{gw_from} → GW{gw_to}: "
        f"{len(moves)} moves, hit={transfer_cost}, "
        f"{'HOLD: ' + hold_reason if hold_reason else 'transferred'}"
    )
    return current_state()


class _IllegalMove(Exception):
    """Recommended move violates a hard FPL rule (bank/3-per-club/shape)."""


def _execute_moves(
    state: Dict, moves: List[Dict], live_prices: Dict[int, float], fpl,
) -> Tuple[List[int], Dict[int, float], float]:
    """
    Apply the list of {out_id, in_id} in order, validating after each move.

    Returns (new_players_ordered, new_purchase_prices, new_bank).

    Selling price rule (FPL):
      profit = current - purchase; if profit > 0 -> sell = purchase + floor(profit/2)
      else -> sell = current  (loss taken in full)
    """
    players = list(state["players"])
    prices = dict(state["purchase_prices"])
    bank = float(state["bank"])
    all_players = {p.id: p for p in fpl.get_players()}

    for move in moves:
        out_id, in_id = move["out_id"], move["in_id"]
        if out_id not in players:
            raise _IllegalMove(f"out_id {out_id} not in current squad")
        if in_id in players:
            raise _IllegalMove(f"in_id {in_id} already in squad")
        out_p = all_players.get(out_id)
        in_p = all_players.get(in_id)
        if out_p is None or in_p is None:
            raise _IllegalMove(f"unknown player id ({out_id} -> {in_id})")
        if out_p.element_type != in_p.element_type:
            raise _IllegalMove(
                f"positional mismatch: {out_p.web_name} ({out_p.element_type}) "
                f"-> {in_p.web_name} ({in_p.element_type})"
            )

        purchase = prices.get(out_id, live_prices.get(out_id, out_p.price))
        current_price = live_prices.get(out_id, out_p.price)
        sell_price = _selling_price(purchase, current_price)
        buy_price = live_prices.get(in_id, in_p.price)

        new_bank = round(bank + sell_price - buy_price, 1)
        if new_bank < -0.05:   # tolerate float noise
            raise _IllegalMove(
                f"insufficient funds for {out_p.web_name} → {in_p.web_name} "
                f"(need £{buy_price:.1f}m, have £{(bank + sell_price):.1f}m)"
            )

        # Apply
        players = [pid if pid != out_id else in_id for pid in players]
        prices.pop(out_id, None)
        prices[in_id] = buy_price
        bank = new_bank

    # Post-move legality checks (3-per-club and positional shape)
    team_counts = Counter(all_players[pid].team for pid in players if pid in all_players)
    over = [t for t, n in team_counts.items() if n > MAX_PER_CLUB]
    if over:
        raise _IllegalMove(f">3 players from team(s) {over}")

    pos_counts = Counter(all_players[pid].element_type for pid in players if pid in all_players)
    if any(pos_counts.get(pid, 0) != n for pid, n in POSITIONAL_SHAPE.items()):
        raise _IllegalMove(
            f"illegal positional shape after transfers: {dict(pos_counts)} "
            f"(expected 2/5/5/3)"
        )

    return players, prices, bank


def _selling_price(purchase: float, current: float) -> float:
    """FPL rule: profit is halved (rounded down in £0.1m units); loss is full."""
    profit = round(current - purchase, 1)
    if profit <= 0:
        return current
    # Round DOWN in £0.1m units — 0.7 profit -> 0.3 gained, not 0.35
    tenths = math.floor((profit * 10) / 2)
    return round(purchase + tenths / 10.0, 1)


def _pick_captain(
    squad: List[int], ranking: List[int], flagged: Set[int], fallback: int,
) -> int:
    for pid in ranking:
        if pid in squad and pid not in flagged:
            return pid
    if fallback in squad:
        return fallback
    return squad[0]


def _pick_vice(
    squad: List[int], ranking: List[int], flagged: Set[int], captain_id: int,
) -> Optional[int]:
    for pid in ranking:
        if pid in squad and pid not in flagged and pid != captain_id:
            return pid
    for pid in squad:
        if pid != captain_id:
            return pid
    return None


# ==================== Scoring a finished GW ====================

def score_finished_gw(gameweek: int) -> Dict[str, Any]:
    """
    Score a finished gameweek's tracked squad against actual FPL points.

    Applies captain doubling (or tripling for Triple Captain), autosubs,
    and Bench Boost. Idempotent: re-running is a no-op after `scored_at`
    is set.
    """
    deps = get_dependencies()
    db = deps.db_manager
    fpl = deps.fpl_client

    existing = db.get_tracked_squad_gw(gameweek)
    if existing and existing.get("scored_at"):
        logger.info(f"Tracked GW{gameweek} already scored; skipping.")
        return existing

    # Only score gameweeks whose data has actually finished per FPL.
    gw_meta = next((gw for gw in fpl.get_gameweeks() if gw.id == gameweek), None)
    if not gw_meta or not getattr(gw_meta, "finished", False):
        logger.info(f"GW{gameweek} not finished yet; nothing to score.")
        return existing or {}

    state = db.get_tracked_squad_state(gameweek=gameweek)
    if not state:
        raise ValueError(
            f"No tracked-squad state for GW{gameweek} — nothing to score."
        )

    stats = fpl.get_event_live_stats(gameweek)
    all_players = {p.id: p for p in fpl.get_players()}
    picks = state["players"]
    starting = picks[:11]
    bench = picks[11:]

    starting_pts, autosubs = _apply_autosubs(starting, bench, stats, all_players)
    bench_pts = sum((stats.get(pid, {}).get("total_points") or 0) for pid in bench)

    captain_id = state["captain_id"]
    vice_id = state.get("vice_id")
    chip = state.get("chip_active")

    # Captain rule: if captain didn't play, vice takes over.
    cap_played = (stats.get(captain_id, {}).get("minutes") or 0) > 0
    effective_captain = captain_id if cap_played else (vice_id or captain_id)
    captain_multiplier = 3 if chip == "triple_captain" else 2
    captain_base = stats.get(effective_captain, {}).get("total_points") or 0
    # The captain's base points are already in starting_pts (or in bench_pts if
    # autosubbed OFF, which doesn't happen). Add the multiplier delta:
    captain_bonus = captain_base * (captain_multiplier - 1)

    if chip == "bench_boost":
        gw_pts = starting_pts + bench_pts + captain_bonus
    elif chip == "free_hit":
        # Free Hit: use this GW's squad only; scoring is otherwise identical.
        gw_pts = starting_pts + captain_bonus
    else:
        gw_pts = starting_pts + captain_bonus

    # Template baseline
    try:
        avg_score = next(
            (gw.average_entry_score for gw in fpl.get_gameweeks() if gw.id == gameweek),
            None,
        )
    except Exception:
        avg_score = None

    db.save_tracked_squad_gw(
        gameweek=gameweek,
        points_scored=int(gw_pts),
        captain_points=int(captain_base * captain_multiplier),
        bench_points=int(bench_pts),
        autosubs=autosubs,
        average_score=avg_score,
        scored_at=datetime.utcnow(),
    )
    logger.info(
        f"Scored tracked GW{gameweek}: {int(gw_pts)} pts "
        f"(captain {effective_captain} x{captain_multiplier}={int(captain_base*captain_multiplier)}, "
        f"bench {int(bench_pts)}, {len(autosubs)} autosubs)"
    )
    return db.get_tracked_squad_gw(gameweek)


def _apply_autosubs(
    starting: List[int],
    bench: List[int],
    stats: Dict[int, Dict],
    all_players: Dict[int, Any],
) -> Tuple[int, List[Dict]]:
    """
    Apply FPL autosub rules and return (points_from_effective_XI, sub_log).

    Rules (simplified but correct for the common cases):
    - A starter who played 0 minutes is replaced by the FIRST bench player
      in sub order who (a) played and (b) keeps the XI legal (>=1 GK, >=3
      DEF, >=1 FWD; MID has no floor other than what the shape implies).
    - The GK bench slot only replaces the GK.
    """
    total = 0
    subs: List[Dict] = []
    effective = list(starting)
    used_bench: Set[int] = set()

    for i, pid in enumerate(effective):
        minutes = stats.get(pid, {}).get("minutes") or 0
        if minutes > 0:
            total += stats.get(pid, {}).get("total_points") or 0
            continue

        # Starter blanked — try to autosub
        starter_pos = getattr(all_players.get(pid), "element_type", None)
        if starter_pos is None:
            continue

        replacement = _find_replacement(
            effective, i, bench, used_bench, stats, all_players, starter_pos,
        )
        if replacement is None:
            # No legal sub — starter contributes their zero, keep going
            continue

        used_bench.add(replacement)
        effective[i] = replacement
        total += stats.get(replacement, {}).get("total_points") or 0
        subs.append({"out_id": pid, "in_id": replacement})

    return total, subs


def _find_replacement(
    effective: List[int],
    starter_idx: int,
    bench: List[int],
    used: Set[int],
    stats: Dict[int, Dict],
    all_players: Dict[int, Any],
    starter_pos: int,
) -> Optional[int]:
    """Pick the first bench player who played and keeps the XI legal."""
    is_gk = starter_pos == 1
    for pid in bench:
        if pid in used:
            continue
        bench_pos = getattr(all_players.get(pid), "element_type", None)
        if bench_pos is None:
            continue
        # GK slot only takes GK
        if is_gk and bench_pos != 1:
            continue
        if not is_gk and bench_pos == 1:
            continue
        if (stats.get(pid, {}).get("minutes") or 0) <= 0:
            continue

        # Check the resulting XI is a legal formation (>=1 GK, >=3 DEF, >=1 FWD)
        trial = [
            all_players[x].element_type
            for x in (effective[:starter_idx] + [pid] + effective[starter_idx + 1:])
            if x in all_players
        ]
        counts = Counter(trial)
        if counts.get(1, 0) < 1 or counts.get(2, 0) < 3 or counts.get(4, 0) < 1:
            continue
        return pid
    return None


# ==================== Reset ====================

def reset() -> int:
    """Wipe the tracked squad (dev/reseed). Returns rows deleted."""
    return get_dependencies().db_manager.reset_tracked_squad()
