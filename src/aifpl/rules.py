from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, Field


Position = Literal["GK", "DEF", "MID", "FWD"]
SQUAD_REQUIREMENTS: dict[Position, int] = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PLAYERS_PER_CLUB = 3
DEFAULT_BUDGET_TENTHS = 1000


class SquadPlayer(BaseModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1)
    position: Position
    club: str = Field(min_length=1)
    cost: int = Field(ge=0, description="FPL cost in tenths of a million pounds")
    projected_points: float


class SquadRequest(BaseModel):
    players: list[SquadPlayer]
    budget: int = Field(default=DEFAULT_BUDGET_TENTHS, ge=0)


class SquadValidation(BaseModel):
    legal: bool
    total_cost: int
    position_counts: dict[str, int]
    club_counts: dict[str, int]
    errors: list[str]


class LineupRecommendation(BaseModel):
    starters: list[SquadPlayer]
    bench: list[SquadPlayer]
    captain: SquadPlayer
    vice_captain: SquadPlayer
    projected_points: float
    formation: str


class TransferCostRequest(BaseModel):
    transfers_made: int = Field(ge=0)
    free_transfers: int = Field(ge=0, le=5)
    unlimited_transfers: bool = False


def validate_squad(squad: SquadRequest) -> SquadValidation:
    players = squad.players
    errors: list[str] = []
    ids = [player.id for player in players]
    if len(set(ids)) != len(ids):
        errors.append("Each squad player ID must be unique")
    if len(players) != 15:
        errors.append(f"A squad must contain exactly 15 players; received {len(players)}")
    position_counts = Counter(player.position for player in players)
    for position, required in SQUAD_REQUIREMENTS.items():
        if position_counts[position] != required:
            errors.append(f"Squad requires exactly {required} {position}; received {position_counts[position]}")
    club_counts = Counter(player.club for player in players)
    for club, count in sorted(club_counts.items()):
        if count > MAX_PLAYERS_PER_CLUB:
            errors.append(f"Squad has {count} players from {club}; maximum is {MAX_PLAYERS_PER_CLUB}")
    total_cost = sum(player.cost for player in players)
    if total_cost > squad.budget:
        errors.append(f"Squad costs {total_cost}, exceeding budget {squad.budget}")
    return SquadValidation(
        legal=not errors,
        total_cost=total_cost,
        position_counts=dict(position_counts),
        club_counts=dict(club_counts),
        errors=errors,
    )


def select_best_lineup(squad: SquadRequest) -> LineupRecommendation:
    validation = validate_squad(squad)
    if not validation.legal:
        raise ValueError("Cannot select a lineup from an illegal squad: " + "; ".join(validation.errors))
    by_position: dict[Position, list[SquadPlayer]] = {
        position: [player for player in squad.players if player.position == position]
        for position in SQUAD_REQUIREMENTS
    }
    best: tuple[float, tuple[SquadPlayer, ...], tuple[int, int, int]] | None = None
    for defender_count, midfielder_count, forward_count in legal_formations():
        for goalkeeper in combinations(by_position["GK"], 1):
            for defenders in combinations(by_position["DEF"], defender_count):
                for midfielders in combinations(by_position["MID"], midfielder_count):
                    for forwards in combinations(by_position["FWD"], forward_count):
                        starters = goalkeeper + defenders + midfielders + forwards
                        projection = sum(player.projected_points for player in starters)
                        if best is None or projection > best[0]:
                            best = (projection, starters, (defender_count, midfielder_count, forward_count))
    assert best is not None
    projected_points, starters_tuple, formation = best
    starters = sorted(starters_tuple, key=lambda player: ("GK", "DEF", "MID", "FWD").index(player.position))
    starter_ids = {player.id for player in starters}
    bench = sorted(
        (player for player in squad.players if player.id not in starter_ids),
        key=lambda player: (player.position == "GK", -player.projected_points, player.id),
    )
    captain, vice_captain = sorted(starters, key=lambda player: (-player.projected_points, player.id))[:2]
    return LineupRecommendation(
        starters=starters,
        bench=bench,
        captain=captain,
        vice_captain=vice_captain,
        projected_points=round(projected_points, 4),
        formation=f"{formation[0]}-{formation[1]}-{formation[2]}",
    )


def legal_formations() -> list[tuple[int, int, int]]:
    """All FPL-valid outfield combinations: 1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD."""
    return [
        (defenders, midfielders, forwards)
        for defenders in range(3, 6)
        for midfielders in range(2, 6)
        for forwards in range(1, 4)
        if defenders + midfielders + forwards == 10
    ]


def transfer_hit_cost(request: TransferCostRequest) -> int:
    """Return points deducted for confirmed transfers; unlimited chips cost no hits."""
    if request.unlimited_transfers:
        return 0
    return max(0, request.transfers_made - request.free_transfers) * 4
