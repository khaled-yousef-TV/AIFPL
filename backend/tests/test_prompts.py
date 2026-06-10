"""Tests for Hermes prompt assembly (pure, given agent reports)."""

from datetime import datetime

from agents.schemas import AgentReport
from hermes.prompts import (
    RUN_TYPE_INSTRUCTIONS,
    SYSTEM_PROMPT,
    assemble_user_prompt,
    render_players,
)


def report(agent, payload, summary="s", status="ok"):
    return AgentReport(
        agent=agent, gameweek=20, generated_at=datetime.utcnow(),
        status=status, summary=summary, payload=payload,
    )


PLAYERS = [
    {"id": 449, "name": "B.Fernandes", "team": "MUN", "position": "MID",
     "price": 8.5, "predicted_points": 6.2, "ownership": 25.0, "form": 7.0, "in_user_team": True},
    {"id": 64, "name": "Watkins", "team": "AVL", "position": "FWD",
     "price": 9.0, "predicted_points": 5.8, "ownership": 13.0, "form": 6.0},
]


def base_reports(season_phase="mid"):
    return {
        "data": report("data", {"players": PLAYERS, "season_phase": season_phase}),
        "mechanics": report("mechanics", {"season_phase": season_phase, "next_gameweek": 20}),
    }


def test_render_players_is_id_keyed_one_line_each():
    out = render_players(PLAYERS, limit=10)
    lines = out.splitlines()
    assert lines[0].startswith("id|name|team")
    assert "449|B.Fernandes|MUN|MID" in lines[1]
    assert len(lines) == 3  # header + 2 players


def test_render_players_adds_prior_column_only_when_present():
    assert "priorPPG" not in render_players(PLAYERS, 10)
    with_prior = [{**PLAYERS[0], "prior_ppg": 5.5}]
    rendered = render_players(with_prior, 10)
    assert "priorPPG" in rendered.splitlines()[0]
    assert "5.5" in rendered


def test_system_prompt_enforces_id_protocol_and_bounds():
    assert "integer `id`" in SYSTEM_PROMPT
    assert "0.5 and 1.5" in SYSTEM_PROMPT
    assert "JSON" in SYSTEM_PROMPT


def test_run_type_instructions_cover_all_types():
    for rt in ("briefing", "squad", "wildcard", "free_hit",
               "triple_captain", "differentials", "my_team", "season_plan"):
        assert rt in RUN_TYPE_INSTRUCTIONS


def test_preseason_guidance_injected():
    prompt = assemble_user_prompt(base_reports("preseason"), "briefing", 1)
    assert "PRESEASON" in prompt
    assert "prior_ppg" in prompt or "last-season" in prompt


def test_mid_season_has_no_phase_warning():
    prompt = assemble_user_prompt(base_reports("mid"), "briefing", 20)
    assert "PRESEASON" not in prompt and "OFF-SEASON" not in prompt


def test_captain_candidate_list_rendered_and_constrained():
    prompt = assemble_user_prompt(base_reports(), "triple_captain", 20, captain_candidates=[449, 64])
    assert "candidate list" in prompt.lower()
    assert "449" in prompt
    # run-type instruction present
    assert RUN_TYPE_INSTRUCTIONS["triple_captain"][:20] in prompt


def test_memory_digest_included_when_present():
    prompt = assemble_user_prompt(
        base_reports(), "briefing", 20, memory_digest="Your boosts hit 60%.",
    )
    assert "Your boosts hit 60%." in prompt


def test_user_squad_section_when_user_players_present():
    prompt = assemble_user_prompt(base_reports(), "my_team", 20)
    # B.Fernandes is flagged in_user_team -> user squad subsection appears
    assert "current squad" in prompt.lower()
