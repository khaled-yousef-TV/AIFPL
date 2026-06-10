"""Regression tests for bugs found in the code-review pass."""

from types import SimpleNamespace

from agents.availability_agent import AvailabilityAgent
from services.squad_service import _greedy_fallback, assemble_squad_result


# --- Bug 2: 0% chance must sort as MORE urgent than 50%, not less ---

def test_availability_sort_treats_zero_chance_as_most_urgent():
    from agents.base import AgentContext

    def player(pid, chance, status="d"):
        return SimpleNamespace(
            id=pid, web_name=f"P{pid}", second_name="X", team=1,
            element_type=3, position="MID", minutes=1000, selected_by_percent=10.0,
            status=status, chance_of_playing_next_round=chance, news="doubt",
        )

    class FakeClient:
        def get_players(self): return [player(1, 50), player(2, 0), player(3, 75)]
        def get_teams(self): return [SimpleNamespace(id=1, short_name="ARS")]
        def get_next_gameweek(self): return SimpleNamespace(id=20, deadline_time=None)

    ctx = AgentContext(fpl_client=FakeClient(), feature_engineer=None, predictor=None,
                       gameweek=20)
    report = AvailabilityAgent().run(ctx)
    chances = [f["chance_of_playing"] for f in report.payload["flagged"]]
    # ascending by chance: 0% first (most out), then 50, then 75
    assert chances == sorted(chances)
    assert chances[0] == 0


# --- Bug 4: greedy fallback must keep locked players ---

def _pool():
    pool = []
    pid = 1
    for pos_id, n in [(1, 4), (2, 8), (3, 8), (4, 6)]:
        for _ in range(n):
            pool.append({"id": pid, "position_id": pos_id, "team_id": pid % 6,
                         "price": 4.5, "predicted": 1.0, "name": f"P{pid}"})
            pid += 1
    return pool


def test_greedy_fallback_seeds_locked_players():
    pool = _pool()
    locked = [pool[0]["id"], pool[5]["id"]]  # one GK, one DEF
    squad = _greedy_fallback(pool, budget=100.0, locked_ids=locked)
    squad_ids = {p["id"] for p in squad}
    assert set(locked).issubset(squad_ids)   # locks survive the fallback


# --- Bug 5: over-constrained inputs degrade, not crash on starting_xi[1] ---

def test_assemble_squad_result_handles_single_player_xi_without_indexerror():
    # 1 GK only: optimize_lineup yields a 1-player XI. Pre-fix this crashed on
    # `sorted(...)[1]` (IndexError); now vice falls back to the captain.
    tiny = [{"id": 1, "position_id": 1, "team_id": 1, "price": 4.0,
             "predicted": 2.0, "name": "Solo"}]
    result = assemble_squad_result(tiny, budget=100.0, method_name="hermes", gameweek=20)
    assert result["captain"]["id"] == 1
    assert result["vice_captain"]["id"] == 1   # vice == captain when only one starter


def test_assemble_squad_result_raises_clean_error_when_no_xi():
    # No players at all -> empty XI -> clean ValueError instead of max()/[1] crash
    try:
        assemble_squad_result([], budget=100.0, method_name="hermes", gameweek=20)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "starting XI" in str(e)
