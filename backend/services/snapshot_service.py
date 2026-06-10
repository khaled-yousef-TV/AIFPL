"""
Daily snapshot squad-validation logic.

Extracted from api/main.py so the "is this squad fit to save?" rules live
in one testable place. A snapshot squad should not contain players who are
injured/suspended/doubtful, unlikely to play, or carry negative news.
"""

import logging
from typing import Dict, List

from agents.availability_agent import has_negative_news
from constants import PlayerStatus

logger = logging.getLogger(__name__)

# Statuses that disqualify a player from a saved snapshot
UNAVAILABLE_STATUSES = [
    PlayerStatus.INJURED, PlayerStatus.SUSPENDED, PlayerStatus.UNAVAILABLE,
    PlayerStatus.NOT_AVAILABLE, PlayerStatus.DOUBTFUL,
]
# Minimum chance-of-playing to be considered safe
MIN_CHANCE_OF_PLAYING = 50


def find_invalid_squad_players(squad_data: Dict, players) -> List[str]:
    """
    Return human-readable descriptions of any unfit players in a squad.

    Args:
        squad_data: SuggestedSquad dict (starting_xi + bench)
        players: list of Player models (fresh from the FPL client)

    Returns:
        List of "Name (reason)" strings; empty if the squad is fully valid.
    """
    player_dict = {p.id: p for p in players}
    squad_ids = [
        p.get("id")
        for p in squad_data.get("starting_xi", []) + squad_data.get("bench", [])
    ]

    invalid = []
    for pid in squad_ids:
        player = player_dict.get(pid)
        if not player:
            continue
        if player.status in UNAVAILABLE_STATUSES:
            invalid.append(f"{player.web_name} {player.second_name} (status: {player.status})")
            continue
        chance = player.chance_of_playing_next_round
        if chance is not None and chance < MIN_CHANCE_OF_PLAYING:
            invalid.append(f"{player.web_name} {player.second_name} (chance: {chance}%)")
            continue
        if has_negative_news(player.news):
            invalid.append(f"{player.web_name} {player.second_name} (news: {player.news[:50]})")

    return invalid
