"""
Availability agent.

Flags players with injury/suspension/availability doubts and European
rotation risk. Single home for the news-keyword logic previously
duplicated in the daily snapshot job.
"""

import logging
from typing import Tuple

from pydantic import BaseModel

from constants import PlayerStatus
from data.european_teams import assess_rotation_risk

from .base import AgentContext, BaseAgent
from .schemas import AvailabilityFlag, AvailabilitySignals

logger = logging.getLogger(__name__)

# Keywords in the FPL `news` field that indicate a player is likely out.
# (Extracted from the daily snapshot validation in api/main.py.)
NEWS_NEGATIVE_KEYWORDS = [
    "injured", "injury", "suspended", "unavailable",
    "ruled out", "will miss", "out for",
]

NON_AVAILABLE_STATUSES = [
    PlayerStatus.DOUBTFUL, PlayerStatus.INJURED, PlayerStatus.SUSPENDED,
    PlayerStatus.UNAVAILABLE, PlayerStatus.NOT_AVAILABLE,
]


def has_negative_news(news: str) -> bool:
    """True if the FPL news text contains a known negative keyword."""
    news_lower = (news or "").lower()
    return any(k in news_lower for k in NEWS_NEGATIVE_KEYWORDS)


class AvailabilityAgent(BaseAgent):
    name = "availability"

    def _build(self, ctx: AgentContext) -> Tuple[str, BaseModel, str]:
        client = ctx.fpl_client
        players = client.get_players()
        teams = client.get_teams()
        short_names = {t.id: t.short_name for t in teams}
        user_ids = set(ctx.user_player_ids)

        next_gw = client.get_next_gameweek()
        deadline = next_gw.deadline_time if next_gw else None

        rotation_cache = {}

        def rotation_for(team_short: str):
            if team_short not in rotation_cache:
                rotation_cache[team_short] = assess_rotation_risk(team_short, deadline)
            return rotation_cache[team_short]

        flagged = []
        for p in players:
            # Skip irrelevant fringe players to keep the signal clean
            relevant = (
                p.minutes > 0
                or float(p.selected_by_percent) >= 1.0
                or p.id in user_ids
            )
            if not relevant:
                continue

            reasons = []
            if p.status in NON_AVAILABLE_STATUSES:
                reasons.append(f"status={p.status}")
            chance = p.chance_of_playing_next_round
            if chance is not None and chance < 100:
                reasons.append(f"chance={chance}%")
            if has_negative_news(p.news):
                reasons.append("negative news")

            team_short = short_names.get(p.team, "???")
            rot = rotation_for(team_short)

            # Rotation-only flags are noisy: only surface for players that matter
            rotation_only = not reasons and rot.risk_level in ("medium", "high")
            if rotation_only and not (
                p.id in user_ids
                or float(p.selected_by_percent) >= 5.0
                or p.minutes >= 900
            ):
                continue

            if reasons or rotation_only:
                flagged.append(AvailabilityFlag(
                    id=p.id,
                    name=p.web_name,
                    team=team_short,
                    status=p.status,
                    chance_of_playing=chance,
                    news=(p.news or "")[:120],
                    rotation_risk=rot.risk_level,
                    rotation_reason=rot.reason if rot.risk_level != "none" else "",
                    flag_reason="; ".join(reasons) if reasons else "rotation risk",
                ))

        # Sort: hard outs first, then doubts, then rotation
        status_order = {
            PlayerStatus.INJURED: 0, PlayerStatus.SUSPENDED: 0,
            PlayerStatus.UNAVAILABLE: 0, PlayerStatus.NOT_AVAILABLE: 0,
            PlayerStatus.DOUBTFUL: 1,
        }
        def _urgency(f):
            # 0% chance is "more out" than 50% — don't treat falsy 0 as 100
            chance = f.chance_of_playing if f.chance_of_playing is not None else 100
            return (status_order.get(f.status, 2), chance)

        flagged.sort(key=_urgency)

        high_rotation = sorted(
            t for t, r in rotation_cache.items() if r.risk_level == "high"
        )

        payload = AvailabilitySignals(
            flagged=flagged,
            high_rotation_risk_teams=high_rotation,
        )

        outs = sum(1 for f in flagged if status_order.get(f.status, 2) == 0)
        doubts = sum(1 for f in flagged if f.status == PlayerStatus.DOUBTFUL)
        summary = (
            f"{len(flagged)} players flagged for GW{ctx.gameweek}: "
            f"{outs} out/suspended, {doubts} doubtful. "
            + (f"High rotation risk: {', '.join(high_rotation)}."
               if high_rotation else "No teams at high rotation risk.")
        )
        return summary, payload, "ok"
