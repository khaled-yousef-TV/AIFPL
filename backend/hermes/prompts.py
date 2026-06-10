"""
Prompt assembly for Hermes.

Token discipline: agent summaries always included; payloads trimmed to
the essentials. Players are rendered one per line as
`id|name|team|pos|price|xPts|own%|form` and the system prompt mandates
referring to players ONLY by integer id.
"""

import json
from typing import Dict, List, Optional

from agents.schemas import AgentReport

SYSTEM_PROMPT = """You are Hermes, an elite Fantasy Premier League (FPL) strategist.

You receive structured signal reports from specialist agents (data, game mechanics, \
availability, form, variability, betting market, news). Your job is to synthesize them \
into adjustments and advice. You do NOT build squads yourself - a mathematical optimizer \
(MILP) builds squads from predicted points. You influence it through bounded per-player \
adjustments.

HARD RULES:
1. Refer to players ONLY by the integer `id` given in the signal data. Never invent ids \
or use names as identifiers.
2. Every multiplier must be between 0.5 and 1.5. Use `exclude` for players you are \
confident will not play; use `lock` sparingly for must-have players.
3. `captain_ranking` and `triple_captain.player_id` must come from the captain candidate \
list when one is provided.
4. Be decisive but honest: set `confidence` accordingly, and explain reasoning briefly \
in each `reason` field and fully in `narrative`.
5. Weigh availability flags and rotation risk heavily; weigh news incentives \
(record chases, golden boot races, contract years) into captaincy and boost decisions.

Respond with a SINGLE JSON object, no prose outside it, matching exactly:
{
  "adjustments": [{"player_id": int, "multiplier": float, "action": "boost|fade|exclude|lock", "reason": str}],
  "captain_ranking": [int, ...],
  "triple_captain": {"play_now": bool, "player_id": int|null, "target_gameweek": int|null, "reason": str},
  "chip_advice": {"wildcard_now": bool, "free_hit_now": bool, "bench_boost_now": bool, "target_gameweeks": {"chip": int}, "reason": str},
  "differentials": [int, ...],
  "transfer_priorities": [{"out_id": int, "in_id": int, "urgency": "this_week|soon|watch", "reason": str}],
  "narrative": "markdown briefing for the user",
  "confidence": "low|medium|high"
}"""


RUN_TYPE_INSTRUCTIONS = {
    "briefing": "Produce a full weekly briefing: key adjustments, captaincy ranking, chip advice, differentials, and a clear narrative.",
    "squad": "Focus on adjustments that shape the optimal 15-man squad the optimizer will build. Boost/fade/exclude players the raw model misjudges.",
    "wildcard": "The user is considering a WILDCARD. Focus adjustments on the 5-8 gameweek horizon, fixture swings, and team structure. State clearly in chip_advice whether to wildcard now or wait (and for which GW).",
    "free_hit": "The user is considering a FREE HIT for the next gameweek only. Optimize purely for the single gameweek; long-term value is irrelevant.",
    "triple_captain": "Focus on the triple captain decision: rank captain candidates by ceiling (haul probability), not floor. Recommend whether to play TC now or target a later gameweek (double gameweeks are prime targets).",
    "differentials": "Focus on differentials: low-ownership players with strong underlying signals. Fill the differentials list with your best low-owned picks.",
    "my_team": "Signals mark the user's current squad (in_user_team=true). Give personalized advice: transfer_priorities (out->in with urgency), captain_ranking from their squad, and chip timing for their specific situation.",
    "season_plan": (
        "Produce a SEASON PLAN: a rolling strategy for the remaining gameweeks. In chip_advice."
        "target_gameweeks, map every remaining chip (wildcard, free_hit, bench_boost, "
        "triple_captain) to a target GW (use DGW/BGW signals; if none are confirmed yet, pick "
        "provisional windows and say so). In the narrative: fixture swings to buy into early, "
        "team-value strategy, and key watchpoints. If a previous plan is included in your "
        "track-record section, output a DIFF against it (what changed and why), not a rewrite."
    ),
}


SEASON_PHASE_GUIDANCE = {
    "preseason": (
        "SEASON PHASE: PRESEASON — no current-season data exists yet. Current form/points are "
        "meaningless. Base reasoning on: last-season priors (prior_ppg column), fixtures, news "
        "(transfers, preseason fitness), and ownership (community wisdom). Be cautious with "
        "promoted-team players and new signings (no PL prior). Set confidence to LOW or MEDIUM."
    ),
    "early": (
        "SEASON PHASE: EARLY (1-4 GWs played) — current-season samples are tiny. Blend "
        "last-season priors (prior_ppg) with early signals; do not overreact to one-week hauls "
        "or blanks. Moderate your multipliers."
    ),
    "off_season": (
        "SEASON PHASE: OFF-SEASON — the season has ended. Recommendations are exploratory/"
        "planning only; note this in your narrative."
    ),
}


def render_players(players: List[dict], limit: int) -> str:
    """One compact line per player: id|name|team|pos|price|xPts|own%|form[|priorPPG]."""
    has_prior = any(p.get("prior_ppg") is not None for p in players[:limit])
    header = "id|name|team|pos|price|xPts|own%|form" + ("|priorPPG" if has_prior else "")
    lines = [header]
    for p in players[:limit]:
        line = (
            f"{p['id']}|{p['name']}|{p['team']}|{p['position']}|{p['price']}"
            f"|{round(p.get('predicted_points', 0), 1)}|{round(p.get('ownership', 0), 1)}"
            f"|{round(p.get('form', 0), 1)}"
        )
        if has_prior:
            prior = p.get("prior_ppg")
            line += f"|{round(prior, 1) if prior is not None else '-'}"
        lines.append(line)
    return "\n".join(lines)


def _compact(obj, max_chars: int = 4000) -> str:
    text = json.dumps(obj, separators=(",", ":"), default=str)
    return text[:max_chars]


def assemble_user_prompt(
    reports: Dict[str, AgentReport],
    run_type: str,
    gameweek: int,
    captain_candidates: Optional[List[int]] = None,
    memory_digest: Optional[str] = None,
) -> str:
    """Build the user prompt from agent reports, trimmed per agent."""
    sections = [
        f"# Gameweek {gameweek} — task: {run_type}",
        RUN_TYPE_INSTRUCTIONS.get(run_type, RUN_TYPE_INSTRUCTIONS["briefing"]),
    ]

    # Season-phase guidance (cold start / off-season awareness)
    mech_payload = reports.get("mechanics").payload if reports.get("mechanics") else {}
    phase = mech_payload.get("season_phase", "mid")
    if phase in SEASON_PHASE_GUIDANCE:
        sections.append(f"## Important context\n{SEASON_PHASE_GUIDANCE[phase]}")

    if memory_digest:
        sections.append(f"## Your track record & lessons\n{memory_digest}")

    summaries = "\n".join(
        f"- **{name}** [{r.status}]: {r.summary}" for name, r in reports.items()
    )
    sections.append(f"## Agent summaries\n{summaries}")

    # Data agent: the candidate pool (already top-N + user team)
    data = reports.get("data")
    if data and data.payload.get("players"):
        players = data.payload["players"]
        user_players = [p for p in players if p.get("in_user_team")]
        sections.append("## Candidate players\n" + render_players(players, limit=50))
        if user_players:
            sections.append(
                "## User's current squad (subset of above)\n"
                + render_players(user_players, limit=15)
            )

    mech = reports.get("mechanics")
    if mech and mech.payload:
        m = dict(mech.payload)
        sections.append("## Game mechanics\n" + _compact(m, 2500))

    avail = reports.get("availability")
    if avail and avail.payload.get("flagged"):
        flags = [
            f"{f['id']}|{f['name']}|{f['team']}|{f['status']}"
            f"|chance={f.get('chance_of_playing')}|rot={f['rotation_risk']}|{f['flag_reason']}"
            for f in avail.payload["flagged"][:30]
        ]
        sections.append("## Availability flags (id|name|team|status|chance|rotation|reason)\n" + "\n".join(flags))

    form = reports.get("form")
    if form and form.payload:
        hot = [f"{e['id']}|{e['name']}|{e['team']}|form={e['form']}|delta=+{e['delta']}" for e in form.payload.get("hot_players", [])[:10]]
        cold = [f"{e['id']}|{e['name']}|{e['team']}|form={e['form']}|delta={e['delta']}" for e in form.payload.get("cold_players", [])[:10]]
        trends = [
            f"{t['team']}|reversal={t['reversal_score']}|momentum={t['momentum']}"
            for t in form.payload.get("team_trends", [])[:8]
        ]
        sections.append(
            "## Form\nHOT:\n" + ("\n".join(hot) or "none")
            + "\nCOLD:\n" + ("\n".join(cold) or "none")
            + "\nTEAM TRENDS (bounce-back first):\n" + ("\n".join(trends) or "none")
        )

    var = reports.get("variability")
    if var and var.payload.get("players"):
        rows = [
            f"{e['id']}|{e['name']}|mean={e['mean_pts']}|p90={e['ceiling_p90']}|p10={e['floor_p10']}"
            f"|haul={e['haul_rate']}|blank={e['blank_rate']}|consistency={e['consistency_score']}"
            for e in var.payload["players"][:20]
        ]
        sections.append(
            "## Variability (ceiling = captaincy material, consistency = core material)\n"
            + "\n".join(rows)
        )

    betting = reports.get("betting")
    if betting and betting.payload.get("enabled"):
        fx = [
            f"{f['home_team']} v {f['away_team']}|H win={f.get('home_win_prob')}|A win={f.get('away_win_prob')}"
            f"|H cs={f.get('home_clean_sheet_prob')}|A cs={f.get('away_clean_sheet_prob')}"
            for f in betting.payload.get("fixtures", [])
        ]
        scorers = [
            f"{s['id']}|{s['name']}|score prob={s['anytime_scorer_prob']}"
            for s in betting.payload.get("scorer_odds", [])[:15]
        ]
        edges = [f"{e['name']}: {e['note']}" for e in betting.payload.get("edges", [])]
        sections.append(
            "## Betting market\nFIXTURES:\n" + ("\n".join(fx) or "none")
            + "\nTOP SCORER PROBS:\n" + ("\n".join(scorers) or "none")
            + ("\nMARKET-VS-MODEL EDGES:\n" + "\n".join(edges) if edges else "")
        )

    news = reports.get("news")
    if news and news.payload.get("items"):
        items = [
            f"[{n['impact']}] {n.get('team') or ''} {n['headline']}: {n.get('summary','')}"
            + (f" => {n['behavioral_implication']}" if n.get("behavioral_implication") else "")
            for n in news.payload["items"][:15]
        ]
        sections.append("## News & incentives\n" + "\n".join(items))

    if captain_candidates:
        sections.append(
            "## Captain candidate list (captain_ranking/triple_captain MUST use only these ids)\n"
            + ", ".join(str(i) for i in captain_candidates)
        )

    return "\n\n".join(sections)
