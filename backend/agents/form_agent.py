"""
Form agent.

Surfaces hot/cold players (recent form vs season baseline) and team-level
momentum/bounce-back signals from data/trends.py.
"""

import logging
from typing import Tuple

from pydantic import BaseModel

from data.trends import compute_team_trends

from .base import AgentContext, BaseAgent
from .schemas import FormEntry, FormSignals, TeamTrendEntry

logger = logging.getLogger(__name__)

# Minimum minutes for a player to be considered an established starter
MIN_MINUTES = 450
# How many hot/cold players to surface
TOP_N = 10


class FormAgent(BaseAgent):
    name = "form"

    def _build(self, ctx: AgentContext) -> Tuple[str, BaseModel, str]:
        client = ctx.fpl_client
        players = client.get_players()
        teams = client.get_teams()
        short_names = {t.id: t.short_name for t in teams}

        entries = []
        for p in players:
            if p.minutes < MIN_MINUTES or p.status != "a":
                continue
            form = float(p.form)
            ppg = float(p.points_per_game)
            entries.append(FormEntry(
                id=p.id,
                name=p.web_name,
                team=short_names.get(p.team, "???"),
                position=p.position,
                form=form,
                points_per_game=ppg,
                delta=round(form - ppg, 2),
            ))

        # Hot: outperforming their own baseline and in genuinely good form
        hot = sorted(
            [e for e in entries if e.delta > 0 and e.form >= 4.0],
            key=lambda e: e.delta, reverse=True,
        )[:TOP_N]
        # Cold: good players (decent baseline) well below their level
        cold = sorted(
            [e for e in entries if e.delta < 0 and e.points_per_game >= 3.5],
            key=lambda e: e.delta,
        )[:TOP_N]

        # Team momentum / bounce-back signals
        fixtures = client.get_fixtures()
        trends = compute_team_trends(teams, fixtures)
        trend_entries = sorted(
            (
                TeamTrendEntry(
                    team=t.short_name,
                    strength=t.strength,
                    season_ppm=t.season_ppm,
                    recent_ppm=t.recent_ppm,
                    momentum=t.momentum,
                    reversal_score=t.reversal_score,
                )
                for t in trends.values()
            ),
            key=lambda t: t.reversal_score,
            reverse=True,
        )

        payload = FormSignals(
            hot_players=hot,
            cold_players=cold,
            team_trends=trend_entries,
        )

        hot_names = ", ".join(e.name for e in hot[:3])
        bounce = ", ".join(t.team for t in trend_entries[:3])
        summary = (
            f"Hot: {hot_names or 'none'}. "
            f"{len(cold)} good players in poor form. "
            f"Top bounce-back teams: {bounce or 'none'}."
        )
        return summary, payload, "ok"
