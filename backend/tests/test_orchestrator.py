"""
Orchestrator-logic tests with a FakeLLM.

Covers the pieces that don't need the DI container / network: run-type
guard, captain-candidate selection, the synthesize + one-shot-repair
flow, and the deterministic fallback narrative. The full run() pipeline
(agents -> MILP) is exercised by the live smoke tests.
"""

import json
from datetime import datetime

import pytest

from agents.schemas import AgentReport
from hermes.config import HermesConfig
from hermes.orchestrator import HermesOrchestrator


CONFIG = HermesConfig(
    enabled=True, base_url="x", model="fake", api_key="x",
    max_output_tokens=2000, timeout_seconds=30, two_pass=False, daily_briefing=False,
)


class FakeLLM:
    """Returns queued responses; records how many times it was called."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, system, user, max_tokens=None):
        self.calls += 1
        resp = self._responses.pop(0)
        return resp, {"prompt_tokens": 10, "completion_tokens": 5}


def report(agent, payload):
    return AgentReport(agent=agent, gameweek=20, generated_at=datetime.utcnow(),
                       status="ok", summary="s", payload=payload)


def reports_with(players, captaincy=None):
    r = {"data": report("data", {"players": players, "season_phase": "mid"}),
         "mechanics": report("mechanics", {"season_phase": "mid", "next_gameweek": 20})}
    if captaincy is not None:
        r["variability"] = report("variability", {"captaincy_candidates": captaincy})
    return r


PLAYERS = [
    {"id": 1, "name": "A", "team": "ARS", "position": "MID", "price": 8.0,
     "predicted_points": 6.0, "ownership": 20, "form": 6, "in_user_team": True},
    {"id": 2, "name": "B", "team": "LIV", "position": "FWD", "price": 9.0,
     "predicted_points": 7.0, "ownership": 30, "form": 7, "in_user_team": False},
]

VALID_JSON = json.dumps({
    "adjustments": [{"player_id": 1, "multiplier": 1.2, "action": "boost", "reason": "x"}],
    "captain_ranking": [2, 1], "differentials": [1], "narrative": "Looks good.",
    "confidence": "high",
})


def test_unknown_run_type_raises():
    orch = HermesOrchestrator(CONFIG, llm_client=FakeLLM([VALID_JSON]))
    with pytest.raises(ValueError):
        orch.run("not_a_real_type")


def test_captain_candidates_blend_data_and_variability():
    reports = reports_with(PLAYERS, captaincy=[99, 2])
    cands = HermesOrchestrator._captain_candidates(reports, None, "briefing")
    assert 1 in cands and 2 in cands
    assert 99 in cands  # variability captaincy candidate merged in


def test_captain_candidates_my_team_restricts_to_user_squad():
    reports = reports_with(PLAYERS, captaincy=[99])
    cands = HermesOrchestrator._captain_candidates(reports, [1], "my_team")
    assert cands == [1]              # only in_user_team players
    assert 99 not in cands           # variability picks excluded in my_team mode


def test_synthesize_valid_first_try():
    llm = FakeLLM([VALID_JSON])
    orch = HermesOrchestrator(CONFIG, llm_client=llm)
    adj, usage = orch._synthesize(reports_with(PLAYERS), "briefing", 20, [1, 2], {1, 2}, None)
    assert llm.calls == 1
    assert adj.captain_ranking == [2, 1]
    assert usage["prompt_tokens"] == 10


def test_synthesize_repairs_after_malformed_json():
    llm = FakeLLM(["not json at all", VALID_JSON])
    orch = HermesOrchestrator(CONFIG, llm_client=llm)
    adj, usage = orch._synthesize(reports_with(PLAYERS), "briefing", 20, [1, 2], {1, 2}, None)
    assert llm.calls == 2                       # one repair retry
    assert usage["prompt_tokens"] == 20         # usage summed across both calls
    assert adj.confidence == "high"


def test_synthesize_raises_after_two_failures():
    llm = FakeLLM(["garbage one", "garbage two"])
    orch = HermesOrchestrator(CONFIG, llm_client=llm)
    with pytest.raises(Exception):
        orch._synthesize(reports_with(PLAYERS), "briefing", 20, [1, 2], {1, 2}, None)
    assert llm.calls == 2


def test_fallback_narrative_lists_agent_summaries():
    reports = {"data": report("data", {}), "form": report("form", {})}
    reports["data"].summary = "10 candidates"
    text = HermesOrchestrator._narrative(None, reports)
    assert "LLM unavailable" in text
    assert "10 candidates" in text


def test_blank_llm_narrative_falls_back_to_agent_summaries():
    """Some models return valid adjustments with narrative=''; the user
    must still see something, but not a misleading 'LLM unavailable'."""
    from hermes.schemas import HermesAdjustments

    reports = {"data": report("data", {})}
    reports["data"].summary = "10 candidates"
    adj = HermesAdjustments(narrative="   ")
    text = HermesOrchestrator._narrative(adj, reports)
    assert "10 candidates" in text
    assert "LLM unavailable" not in text

    adj_with_text = HermesAdjustments(narrative="Captain Salah.")
    assert HermesOrchestrator._narrative(adj_with_text, reports) == "Captain Salah."


def test_no_llm_configured_means_no_client():
    unconfigured = HermesConfig(
        enabled=True, base_url=None, model=None, api_key=None,
        max_output_tokens=2000, timeout_seconds=30, two_pass=False, daily_briefing=False,
    )
    orch = HermesOrchestrator(unconfigured)
    assert orch.llm is None
