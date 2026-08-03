"""
Tracked-squad service tests.

Focus: the pure-executor contract — apply_transfer MUST NOT invent moves
Hermes didn't recommend, must honor hold/degraded verdicts, and must
handle FT roll-up and selling-price arithmetic correctly. The scoring
path is exercised on a mocked live-stats endpoint.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from database.crud import DatabaseManager
from services import tracked_squad_service as svc


# ---------------- fixtures ----------------

@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    manager = DatabaseManager(db_url=f"sqlite:///{path}")
    yield manager
    try:
        os.remove(path)
    except OSError:
        pass


def _fake_player(pid, team, etype, price=5.0, web_name=None):
    return SimpleNamespace(
        id=pid, team=team, element_type=etype, price=price,
        web_name=web_name or f"P{pid}",
    )


def _fake_fpl(players=None, next_gw_id=2):
    """Minimal FPL client stub — enough for apply_transfer and scoring."""
    players = players or [_fake_player(i, (i - 1) % 5 + 1, ((i - 1) % 4) + 1) for i in range(1, 40)]
    fpl = MagicMock()
    fpl.get_players.return_value = players
    # get_teams is used by _player_lookup_for; return a lightweight team list
    fpl.get_teams.return_value = [SimpleNamespace(id=i, short_name=f"T{i}") for i in range(1, 6)]
    fpl.get_next_gameweek.return_value = SimpleNamespace(id=next_gw_id)
    fpl.get_gameweeks.return_value = [SimpleNamespace(id=i, finished=(i < next_gw_id), average_entry_score=50) for i in range(1, 39)]
    fpl.get_event_live_stats.return_value = {}
    return fpl


# Legal 2/5/5/3 shape with ≤3 per team, using the _fake_player id→(team,etype)
# convention (etype=(id-1)%4+1, team=(id-1)%5+1). This is the default seed
# apply_transfer's shape check accepts.
LEGAL_SQUAD_15 = [1, 5, 2, 6, 10, 14, 18, 3, 7, 11, 15, 19, 4, 8, 12]


def _seed_state(db, gw=1, players=None, bank=0.0, ft=1, chips_used=None):
    """Write a starting tracked-squad state to the DB directly."""
    players = players or list(LEGAL_SQUAD_15)
    prices = {pid: 5.0 for pid in players}
    db.save_tracked_squad_state(
        gameweek=gw,
        players=players,
        purchase_prices=prices,
        captain_id=players[0],
        vice_id=players[1],
        bank=bank,
        free_transfers=ft,
        chips_used=chips_used or [],
        chip_active=None,
    )


# ---------------- selling price rule ----------------

def test_selling_price_full_loss():
    """Selling below purchase: loss is taken in full (no half-back rule)."""
    assert svc._selling_price(purchase=6.0, current=5.5) == 5.5


def test_selling_price_profit_halved_rounded_down():
    """0.7 profit -> 0.3 kept (round down in £0.1m units), not 0.35."""
    assert svc._selling_price(purchase=5.0, current=5.7) == 5.3
    assert svc._selling_price(purchase=5.0, current=6.0) == 5.5
    assert svc._selling_price(purchase=5.0, current=5.0) == 5.0


# ---------------- apply_transfer: hold cases ----------------

def _make_deps(db, fpl):
    """Patch service.get_dependencies to return the test DB + FPL stub."""
    deps = SimpleNamespace(db_manager=db, fpl_client=fpl)
    return patch("services.tracked_squad_service.get_dependencies", return_value=deps)


def test_apply_transfer_hold_verdict_advances_ft_up_to_2(db):
    _seed_state(db, gw=1, ft=1)
    fpl = _fake_fpl(next_gw_id=2)
    run = {
        "status": "completed",
        "result": {
            "transfer_plan": {"recommendation": "hold", "reason": "GW4 fixture swing"},
            "transfer_priorities": [],
            "captain_ranking": [{"id": 3, "name": "P3"}],
        },
        "signals": {"availability": {"payload": {"flagged": []}}},
    }
    with _make_deps(db, fpl):
        svc.apply_transfer(run)

    next_state = db.get_tracked_squad_state(gameweek=2)
    assert next_state is not None
    assert next_state["players"] == LEGAL_SQUAD_15       # unchanged
    assert next_state["free_transfers"] == 2             # 1 -> 2 (capped)

    ledger = db.get_tracked_squad_gw(1)
    assert ledger["transfer_cost"] == 0
    # Ledger records intent to hold, not a silent no-op
    assert any(t.get("held") for t in ledger["transfers_made"])


def test_apply_transfer_ft_capped_at_2(db):
    _seed_state(db, gw=1, ft=2)
    fpl = _fake_fpl(next_gw_id=2)
    run = {
        "status": "completed",
        "result": {
            "transfer_plan": {"recommendation": "hold", "reason": "wait"},
            "transfer_priorities": [],
            "captain_ranking": [],
        },
    }
    with _make_deps(db, fpl):
        svc.apply_transfer(run)

    assert db.get_tracked_squad_state(gameweek=2)["free_transfers"] == 2


def test_apply_transfer_degraded_run_forces_hold_regardless_of_priorities(db):
    """Degraded runs must NEVER apply the priorities — a run with no LLM is
    not Hermes advice and we would be measuring the MILP instead."""
    _seed_state(db, gw=1, ft=1)
    fpl = _fake_fpl(next_gw_id=2)
    run = {
        "status": "degraded",   # LLM was unavailable
        "result": {
            "transfer_plan": {"recommendation": "transfer", "reason": "x"},
            "transfer_priorities": [
                {"out_id": 1, "in_id": 20, "urgency": "this_week", "reason": "y"},
            ],
            "captain_ranking": [{"id": 3, "name": "P3"}],
        },
        "signals": {"availability": {"payload": {"flagged": []}}},
    }
    with _make_deps(db, fpl):
        svc.apply_transfer(run)

    next_state = db.get_tracked_squad_state(gameweek=2)
    # Original squad preserved — the recommended out/in DID NOT happen
    assert 1 in next_state["players"]
    assert 20 not in next_state["players"]
    ledger = db.get_tracked_squad_gw(1)
    assert any(t.get("held") for t in ledger["transfers_made"])
    assert "LLM unavailable" in " ".join(
        t.get("reason", "") for t in ledger["transfers_made"] if t.get("held")
    )


# ---------------- apply_transfer: legal transfer ----------------

def test_apply_transfer_executes_this_week_priority_and_updates_bank(db):
    """Recommended transfer with urgency=this_week actually swaps players."""
    _seed_state(db, gw=1, ft=1, bank=1.0)
    fpl = _fake_fpl(next_gw_id=2)
    # Squad GK ids [1, 5] (teams 1, 5). Swap 1 (team 1) -> 21 (team 1, GK).
    # Post-move team 1 members: 6, 11, 21 (3, legal). Shape unchanged.
    # in_id=22 for the 'soon' priority is NOT executed since only this_week counts.
    run = {
        "status": "completed",
        "result": {
            "transfer_plan": {"recommendation": "transfer", "reason": "form"},
            "transfer_priorities": [
                {"out_id": 1, "in_id": 21, "urgency": "this_week", "reason": "hotter"},
                {"out_id": 2, "in_id": 22, "urgency": "soon", "reason": "watch"},
            ],
            "captain_ranking": [{"id": 3, "name": "P3"}],
        },
        "signals": {"availability": {"payload": {"flagged": []}}},
    }
    with _make_deps(db, fpl):
        svc.apply_transfer(run)

    next_state = db.get_tracked_squad_state(gameweek=2)
    assert 21 in next_state["players"]
    assert 1 not in next_state["players"]
    # Only 1 transfer used, 1 FT was available, so no hit
    ledger = db.get_tracked_squad_gw(1)
    assert ledger["transfer_cost"] == 0
    # Second priority ('soon') stays UNEXECUTED
    assert 2 in next_state["players"]
    assert 22 not in next_state["players"]


def test_apply_transfer_hit_cost_when_exceeding_ft(db):
    """1 FT + 2 this_week moves => 1 free + 1 paid (-4)."""
    _seed_state(db, gw=1, ft=1, bank=10.0)
    fpl = _fake_fpl(next_gw_id=2)
    # Two swaps that both keep 2/5/5/3 shape and ≤3 per team:
    #   1 (GK,team1) -> 21 (GK,team1)
    #   2 (DEF,team2) -> 22 (DEF,team2)
    run = {
        "status": "completed",
        "result": {
            "transfer_plan": {"recommendation": "transfer", "reason": "double swap"},
            "transfer_priorities": [
                {"out_id": 1, "in_id": 21, "urgency": "this_week", "reason": "gk swap"},
                {"out_id": 2, "in_id": 22, "urgency": "this_week", "reason": "def swap"},
            ],
            "captain_ranking": [{"id": 3, "name": "P3"}],
        },
        "signals": {"availability": {"payload": {"flagged": []}}},
    }
    with _make_deps(db, fpl):
        svc.apply_transfer(run)

    ledger = db.get_tracked_squad_gw(1)
    # 1 free + 1 paid = 1 hit = -4 pts
    assert ledger["transfer_cost"] == 4


def test_apply_transfer_illegal_move_falls_back_to_hold(db):
    """An illegal transfer (positional mismatch) must hold with a reason,
    NOT silently substitute a legal move Hermes didn't emit."""
    _seed_state(db, gw=1, ft=1)
    fpl = _fake_fpl(next_gw_id=2)
    # id 1 is GK (etype 1); id 4 is FWD (etype 4) — positional mismatch
    run = {
        "status": "completed",
        "result": {
            "transfer_plan": {"recommendation": "transfer", "reason": "form"},
            "transfer_priorities": [
                {"out_id": 1, "in_id": 20, "urgency": "this_week", "reason": "bad"},
            ],
            "captain_ranking": [{"id": 3, "name": "P3"}],
        },
        "signals": {"availability": {"payload": {"flagged": []}}},
    }
    with _make_deps(db, fpl):
        svc.apply_transfer(run)

    next_state = db.get_tracked_squad_state(gameweek=2)
    # Squad unchanged — illegal move held
    assert 1 in next_state["players"]
    assert 20 not in next_state["players"]
    ledger = db.get_tracked_squad_gw(1)
    assert any(t.get("held") and "illegal" in t["reason"].lower()
               for t in ledger["transfers_made"])


def test_apply_transfer_captain_from_ranking_skips_flagged(db):
    """Captain is the first ranked player in the (post-move) squad who is
    NOT availability-flagged. Never a heuristic pick."""
    _seed_state(db, gw=1, ft=1)
    fpl = _fake_fpl(next_gw_id=2)
    run = {
        "status": "completed",
        "result": {
            "transfer_plan": {"recommendation": "hold", "reason": "wait"},
            "transfer_priorities": [],
            "captain_ranking": [{"id": 3, "name": "P3"}, {"id": 7, "name": "P7"}],
        },
        "signals": {"availability": {"payload": {"flagged": [{"id": 3, "name": "P3"}]}}},
    }
    with _make_deps(db, fpl):
        svc.apply_transfer(run)

    next_state = db.get_tracked_squad_state(gameweek=2)
    assert next_state["captain_id"] == 7  # 3 was flagged, skipped


def test_apply_transfer_idempotent_on_double_apply(db):
    """Scheduler retries land here safely — second apply is a no-op."""
    _seed_state(db, gw=1, ft=1)
    fpl = _fake_fpl(next_gw_id=2)
    run = {
        "status": "completed",
        "result": {
            "transfer_plan": {"recommendation": "hold", "reason": "wait"},
            "transfer_priorities": [],
            "captain_ranking": [],
        },
    }
    with _make_deps(db, fpl):
        svc.apply_transfer(run)
        first = db.get_tracked_squad_state(gameweek=2)
        svc.apply_transfer(run)
        second = db.get_tracked_squad_state(gameweek=2)

    assert first == second


# ---------------- seeding ----------------

def test_seed_refuses_when_already_seeded(db):
    _seed_state(db, gw=1)
    fpl = _fake_fpl(next_gw_id=2)
    with _make_deps(db, fpl):
        with pytest.raises(ValueError, match="already seeded"):
            svc.seed_from_best_squad()


def test_seed_refuses_when_no_best_squad_run(db):
    fpl = _fake_fpl(next_gw_id=1)
    with _make_deps(db, fpl):
        with pytest.raises(ValueError, match="No completed Best Squad run"):
            svc.seed_from_best_squad()
