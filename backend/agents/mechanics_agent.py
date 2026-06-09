"""
FPL game mechanics agent.

Knows the rules of the game: deadlines, squad constraints, chips,
double/blank gameweek detection from fixture counts, and price-change
pressure from transfer flows.
"""

import logging
from datetime import datetime, timezone
from typing import Tuple

from pydantic import BaseModel

from .base import AgentContext, BaseAgent
from .schemas import (
    GameweekFixtureLoad,
    MechanicsSignals,
    PriceChangeCandidate,
    SquadRules,
)

logger = logging.getLogger(__name__)

# How many price rise/fall candidates to surface
PRICE_CANDIDATES = 8
# Minimum net transfer flow to be considered price-change pressure
MIN_TRANSFER_BALANCE = 50_000


def detect_fixture_load(gameweek_ids, fixtures, team_short_names):
    """
    Detect double and blank gameweeks from fixture counts per team per GW.

    Args:
        gameweek_ids: list of GW ids to inspect
        fixtures: list of Fixture objects (with .event, .team_h, .team_a)
        team_short_names: dict of team_id -> short name

    Returns:
        List of GameweekFixtureLoad for GWs that have doubles or blanks.
    """
    counts = {gw_id: {tid: 0 for tid in team_short_names} for gw_id in gameweek_ids}
    for f in fixtures:
        if f.event in counts:
            if f.team_h in counts[f.event]:
                counts[f.event][f.team_h] += 1
            if f.team_a in counts[f.event]:
                counts[f.event][f.team_a] += 1

    load = []
    for gw_id in gameweek_ids:
        per_team = counts[gw_id]
        # Skip GWs with no fixtures scheduled yet (can't distinguish blanks)
        if sum(per_team.values()) == 0:
            continue
        doubles = sorted(team_short_names[tid] for tid, n in per_team.items() if n >= 2)
        blanks = sorted(team_short_names[tid] for tid, n in per_team.items() if n == 0)
        if doubles or blanks:
            load.append(GameweekFixtureLoad(
                gameweek=gw_id, double_teams=doubles, blank_teams=blanks
            ))
    return load


class MechanicsAgent(BaseAgent):
    name = "mechanics"

    def _build(self, ctx: AgentContext) -> Tuple[str, BaseModel, str]:
        client = ctx.fpl_client

        gameweeks = client.get_gameweeks()
        current_gw = client.get_current_gameweek()
        next_gw = client.get_next_gameweek()
        teams = client.get_teams()
        short_names = {t.id: t.short_name for t in teams}
        fixtures = client.get_fixtures()

        next_gw_id = next_gw.id if next_gw else (current_gw.id if current_gw else 0)
        remaining_ids = [gw.id for gw in gameweeks if gw.id >= next_gw_id]
        fixture_load = detect_fixture_load(remaining_ids, fixtures, short_names)

        # Deadline
        deadline = next_gw.deadline_time if next_gw else None
        hours_to_deadline = None
        if deadline:
            now = datetime.now(timezone.utc) if deadline.tzinfo else datetime.utcnow()
            hours_to_deadline = round((deadline - now).total_seconds() / 3600.0, 1)

        # Price-change pressure from net transfer flows
        players = client.get_players()
        flows = sorted(
            players,
            key=lambda p: p.transfers_in_event - p.transfers_out_event,
            reverse=True,
        )
        rises = [
            PriceChangeCandidate(
                id=p.id, name=p.web_name, team=short_names.get(p.team, "???"),
                price=p.price,
                transfer_balance=p.transfers_in_event - p.transfers_out_event,
                direction="rise",
            )
            for p in flows[:PRICE_CANDIDATES]
            if p.transfers_in_event - p.transfers_out_event >= MIN_TRANSFER_BALANCE
        ]
        falls = [
            PriceChangeCandidate(
                id=p.id, name=p.web_name, team=short_names.get(p.team, "???"),
                price=p.price,
                transfer_balance=p.transfers_in_event - p.transfers_out_event,
                direction="fall",
            )
            for p in flows[-PRICE_CANDIDATES:][::-1]
            if p.transfers_in_event - p.transfers_out_event <= -MIN_TRANSFER_BALANCE
        ]

        # Chip timing guidance derived from fixture load
        guidance = []
        for fl in fixture_load:
            if fl.double_teams:
                guidance.append(
                    f"GW{fl.gameweek} is a DOUBLE for {', '.join(fl.double_teams)} — "
                    "prime window for Bench Boost / Triple Captain."
                )
            if fl.blank_teams:
                guidance.append(
                    f"GW{fl.gameweek} is a BLANK for {', '.join(fl.blank_teams)} — "
                    "Free Hit candidate if many of these are owned."
                )
        if not guidance:
            guidance.append("No confirmed double or blank gameweeks in scheduled fixtures.")

        payload = MechanicsSignals(
            current_gameweek=current_gw.id if current_gw else 0,
            next_gameweek=next_gw_id,
            next_deadline=deadline,
            hours_to_deadline=hours_to_deadline,
            fixture_load=fixture_load,
            price_rise_candidates=rises,
            price_fall_candidates=falls,
            squad_rules=SquadRules(),
            chip_guidance=guidance,
        )

        dgws = [fl.gameweek for fl in fixture_load if fl.double_teams]
        bgws = [fl.gameweek for fl in fixture_load if fl.blank_teams]
        summary = (
            f"Next deadline GW{next_gw_id}"
            + (f" in {hours_to_deadline}h. " if hours_to_deadline is not None else ". ")
            + (f"DGWs: {dgws}. " if dgws else "No confirmed DGWs. ")
            + (f"BGWs: {bgws}. " if bgws else "No confirmed BGWs. ")
            + f"{len(rises)} price-rise / {len(falls)} price-fall candidates."
        )
        return summary, payload, "ok"
