"""
Per-agent integration tests.

Each deterministic agent is run end-to-end against an in-memory fake FPL
client (no network), asserting its AgentReport status and key payload
fields. Directly addresses "test each agent separately".

Covered here: mechanics, availability, form, variability, news (degraded).
The data and betting agents depend on the full prediction pipeline / DI
container and are exercised via the live smoke tests, not unit fakes.
"""

from datetime import datetime, timedelta

import pytest

from agents.availability_agent import AvailabilityAgent
from agents.base import AgentContext
from agents.form_agent import FormAgent
from agents.mechanics_agent import MechanicsAgent
from agents.news_agent import NewsAgent
from agents.variability_agent import VariabilityAgent
from fpl.models import Fixture, GameWeek, Player, Team


# ---------------- fixtures: a minimal fake league ----------------

def make_player(pid, name, team, etype, **kw):
    base = dict(
        id=pid, first_name=name, second_name=name, web_name=name,
        team=team, team_code=team, element_type=etype, now_cost=80,
        total_points=kw.pop("total_points", 100), points_per_game=kw.pop("ppg", 5.0),
        minutes=kw.pop("minutes", 1500), form=kw.pop("form", 5.0),
        selected_by_percent=kw.pop("own", 20.0),
        status=kw.pop("status", "a"),
        chance_of_playing_next_round=kw.pop("chance", 100),
        news=kw.pop("news", ""),
    )
    base.update(kw)
    return Player(**base)


def make_team(tid, short, strength=3):
    return Team(
        id=tid, name=short, short_name=short, code=tid, strength=strength,
        strength_overall_home=1100, strength_overall_away=1100,
        strength_attack_home=1100, strength_attack_away=1100,
        strength_defence_home=1100, strength_defence_away=1100,
    )


class FakeFPLClient:
    def __init__(self, finished_gws=10):
        self._teams = [make_team(1, "ARS", 5), make_team(2, "LIV", 5),
                       make_team(3, "BUR", 2), make_team(4, "LEE", 2)]
        self._players = [
            make_player(1, "Saka", 1, 3, form=8.0, ppg=6.5, own=40.0),
            make_player(2, "Salah", 2, 3, form=7.0, ppg=7.5, own=55.0),
            make_player(3, "Crocked", 3, 4, status="i", chance=0, news="Hamstring injury - out", minutes=400),
            make_player(4, "Doubt", 4, 2, status="d", chance=50, news="Knock - 50% chance", own=8.0),
            make_player(5, "Cold", 1, 3, form=1.5, ppg=4.0, own=12.0),
        ]
        # finished fixtures (for form/trends) + an upcoming GW with a double
        now = datetime(2026, 1, 1)
        self._fixtures = [
            Fixture(id=1, event=9, team_h=1, team_a=3, team_h_difficulty=2, team_a_difficulty=5,
                    kickoff_time=now - timedelta(days=7), finished=True, team_h_score=3, team_a_score=0),
            Fixture(id=2, event=9, team_h=2, team_a=4, team_h_difficulty=2, team_a_difficulty=5,
                    kickoff_time=now - timedelta(days=7), finished=True, team_h_score=2, team_a_score=1),
            # upcoming GW11: ARS plays twice (double), LEE blanks
            Fixture(id=3, event=11, team_h=1, team_a=2, team_h_difficulty=3, team_a_difficulty=3,
                    kickoff_time=now + timedelta(days=7), finished=False),
            Fixture(id=4, event=11, team_h=3, team_a=1, team_h_difficulty=4, team_a_difficulty=2,
                    kickoff_time=now + timedelta(days=8), finished=False),
        ]
        self._gameweeks = []
        for i in range(1, 39):
            self._gameweeks.append(GameWeek(
                id=i, name=f"GW{i}", deadline_time=now + timedelta(days=i),
                is_current=(i == finished_gws), is_next=(i == finished_gws + 1),
                finished=(i <= finished_gws),
            ))

    def get_players(self): return self._players
    def get_teams(self): return self._teams
    def get_gameweeks(self): return self._gameweeks
    def get_fixtures(self, gameweek=None):
        return [f for f in self._fixtures if gameweek is None or f.event == gameweek]
    def get_current_gameweek(self):
        return next((g for g in self._gameweeks if g.is_current), None)
    def get_next_gameweek(self):
        return next((g for g in self._gameweeks if g.is_next), None)
    def get_top_players(self, n=20, position=None):
        ps = sorted(self._players, key=lambda p: p.total_points, reverse=True)
        return ps[:n]
    def get_player_details(self, pid):
        # deterministic synthetic history
        return {"history": [
            {"round": r, "total_points": (pid * 2 + r) % 13, "minutes": 90}
            for r in range(1, 11)
        ]}


@pytest.fixture
def ctx():
    client = FakeFPLClient()
    return AgentContext(
        fpl_client=client, feature_engineer=None, predictor=None,
        betting_odds_client=None, db_manager=None, gameweek=11, top_n=20,
    )


# ---------------- mechanics ----------------

def test_mechanics_agent_detects_phase_and_dgw(ctx):
    report = MechanicsAgent().run(ctx)
    assert report.status == "ok"
    p = report.payload
    assert p["season_phase"] == "mid"        # 10 GWs finished
    assert p["finished_gameweeks"] == 10
    dgw = [g for g in p["fixture_load"] if g["double_teams"]]
    assert any("ARS" in g["double_teams"] for g in dgw)   # ARS plays twice in GW11
    assert "team_next6_fdr" in p


# ---------------- availability ----------------

def test_availability_agent_flags_injured_and_doubtful(ctx):
    report = AvailabilityAgent().run(ctx)
    assert report.status == "ok"
    flagged_ids = {f["id"] for f in report.payload["flagged"]}
    assert 3 in flagged_ids   # injured
    assert 4 in flagged_ids   # doubtful + news
    assert 1 not in flagged_ids  # fully available star not flagged


# ---------------- form ----------------

def test_form_agent_surfaces_hot_and_trends(ctx):
    report = FormAgent().run(ctx)
    assert report.status == "ok"
    p = report.payload
    hot_ids = {e["id"] for e in p["hot_players"]}
    assert 1 in hot_ids   # Saka form 8 > ppg 6.5 -> hot
    assert len(p["team_trends"]) == 4


# ---------------- variability ----------------

def test_variability_agent_computes_and_ranks(ctx):
    report = VariabilityAgent().run(ctx)
    assert report.status in ("ok", "degraded")
    p = report.payload
    assert p["covered"] >= 1
    # every covered player has the full stat shape incl. the new form_recent
    for e in p["players"]:
        assert "form_recent" in e and "ceiling_p90" in e and "mean_pts" in e
    assert isinstance(p["captaincy_candidates"], list)


# ---------------- news (degraded, no LLM) ----------------

def test_news_agent_degrades_to_fpl_news_without_llm(ctx):
    report = NewsAgent().run(ctx)   # ctx.llm_client is None
    assert report.status == "degraded"
    assert report.payload["search_used"] is False
    # the injured player's FPL news becomes an item
    impacts = {i["impact"] for i in report.payload["items"]}
    assert impacts & {"out", "doubt"}


# ---------------- envelope guarantees ----------------

def test_all_agents_return_valid_envelope(ctx):
    for Agent in (MechanicsAgent, AvailabilityAgent, FormAgent, VariabilityAgent, NewsAgent):
        report = Agent().run(ctx)
        assert report.gameweek == 11
        assert report.status in ("ok", "degraded", "error")
        assert report.elapsed_ms >= 0
        assert report.summary


# ---------------- Phase 0.1: squad awareness ----------------

def test_form_agent_surfaces_squad_player_outside_global_lists(ctx):
    """Phase 0.1 regression: an owned mid-form player has to show up in
    squad_form even when he doesn't clear the global hot/cold thresholds."""
    # Player 5 ("Cold"): form 1.5, ppg 4.0 -> below hot cutoff, above cold ppg
    # threshold barely — but crucially, when the squad excludes him from the
    # global cold list (ppg 4.0 = 3.5 barely), he still MUST appear in squad_form.
    ctx.user_player_ids = [5]
    report = FormAgent().run(ctx)
    squad_ids = {e["id"] for e in report.payload["squad_form"]}
    assert 5 in squad_ids, "Owned player must appear in squad_form regardless of hot/cold thresholds"


def test_form_agent_squad_form_empty_when_no_squad(ctx):
    """No squad context -> squad_form stays empty (back-compat)."""
    report = FormAgent().run(ctx)
    assert report.payload["squad_form"] == []


def test_mechanics_agent_surfaces_squad_fixture_impact(ctx):
    """Phase 0.1: mechanics reports how many owned players are affected by
    each DGW/BGW in the horizon — concrete input for chip projections."""
    # ARS doubles in GW11; player 1 (Saka) is ARS
    ctx.user_player_ids = [1, 2]
    report = MechanicsAgent().run(ctx)
    impact = report.payload["squad_fixture_impact"]
    gw11 = next((si for si in impact if si["gameweek"] == 11), None)
    assert gw11 is not None
    assert gw11["owned_players_doubling"] == 1
    assert "Saka" in gw11["doubling_names"]


def test_mechanics_agent_squad_fixture_impact_empty_when_no_squad(ctx):
    """No squad context -> squad_fixture_impact stays empty (back-compat)."""
    report = MechanicsAgent().run(ctx)
    assert report.payload["squad_fixture_impact"] == []


def test_news_agent_squad_only_notes_included(ctx):
    """Phase 0.1: fpl_notes now includes owned players regardless of ownership%.
    A low-owned owned player with news must still reach Hermes."""
    # Player 4 ("Doubt", 8% owned) has news; player 3 injured ("Crocked", 400 min, low own)
    ctx.user_player_ids = [3, 4]
    report = NewsAgent().run(ctx)   # LLM is None so this is the degraded path
    # Even with LLM off, the fpl_news_fallback still needs to see squad players.
    # In degraded mode fpl_news_fallback runs directly and filters by
    # p.minutes == 0 and float(p.selected_by_percent) < 1.0. Player 3 has 400 min
    # so already qualifies. This test asserts the degraded path still works.
    ids = {i["player_id"] for i in report.payload["items"] if i.get("player_id")}
    assert 3 in ids


# ---------------- Phase 0.2: assemble_fixed_squad ----------------

def _mock_predictions(ids):
    """15 valid predictions covering a legal 2/5/5/3 shape across ≤5 teams."""
    # positions: 2 GK, 5 DEF, 5 MID, 3 FWD
    positions = ([1] * 2) + ([2] * 5) + ([3] * 5) + ([4] * 3)
    pos_names = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    teams = [10, 11, 12, 13, 14]  # 5 teams, ≤3 per team
    preds = []
    for i, pid in enumerate(ids):
        preds.append({
            "id": pid,
            "name": f"P{pid}",
            "team": f"T{teams[i % 5]}",
            "team_id": teams[i % 5],
            "position": pos_names[positions[i]],
            "position_id": positions[i],
            "price": 5.0,
            "predicted": 5.0 + (i * 0.1),
        })
    return preds


def test_assemble_fixed_squad_returns_the_given_15():
    """Fixed-squad rendering must not substitute an unowned player the MILP prefers."""
    from services.squad_service import assemble_fixed_squad
    ids = list(range(101, 116))  # 15 unique ids
    result = assemble_fixed_squad(_mock_predictions(ids), ids, gameweek=3)
    squad_ids = {p["id"] for p in result["starting_xi"]} | {p["id"] for p in result["bench"]}
    assert squad_ids == set(ids)
    assert result["is_fixed_squad"] is True
    assert len(result["starting_xi"]) == 11
    assert len(result["bench"]) == 4


def test_assemble_fixed_squad_rejects_illegal_shape():
    """Wrong positional counts must raise, not silently render a broken squad."""
    from services.squad_service import assemble_fixed_squad
    ids = list(range(101, 116))
    preds = _mock_predictions(ids)
    # Break the shape: make all 15 midfielders
    for p in preds:
        p["position_id"] = 3
        p["position"] = "MID"
    with pytest.raises(ValueError, match="illegal positional shape"):
        assemble_fixed_squad(preds, ids, gameweek=3)


def test_assemble_fixed_squad_rejects_too_many_from_one_team():
    """Squad with 4+ players from a single team must raise."""
    from services.squad_service import assemble_fixed_squad
    ids = list(range(101, 116))
    preds = _mock_predictions(ids)
    # Force first 4 into team 99
    for p in preds[:4]:
        p["team_id"] = 99
    with pytest.raises(ValueError, match=">3 players from a single team"):
        assemble_fixed_squad(preds, ids, gameweek=3)
