from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayer, CurrentPlayerCatalogStore
from aifpl.fixture_projections import DIFFICULTY_MULTIPLIERS
from aifpl.fixtures import CurrentFixture, CurrentFixtureCatalogStore
from aifpl.odds_matching import FixtureOddsConsensus, FixtureOddsConsensusStore
from aifpl.xg_projections import xg_xa_blend


ODDS_PROJECTION_METHOD = "fpl_xg_xa_blend_v1.fixture_difficulty_v1.match_odds_v1"
ODDS_WIN_WEIGHT = 0.4


@dataclass(frozen=True)
class OddsAdjustedGameweekProjection:
    player_id: int
    player_name: str
    position: str
    club: str
    cost: int
    gameweek: int
    fixture_count: int
    odds_backed_fixture_count: int
    projected_points: float
    methodology: str = ODDS_PROJECTION_METHOD


@dataclass(frozen=True)
class OddsProjectionCatalog:
    start_gameweek: int
    end_gameweek: int
    records: int
    output_path: str
    created_at: datetime


def build_odds_adjusted_projections(
    players: list[CurrentPlayer], fixtures: list[CurrentFixture], consensus: list[FixtureOddsConsensus], start_gameweek: int, end_gameweek: int
) -> list[OddsAdjustedGameweekProjection]:
    if start_gameweek < 1 or end_gameweek < start_gameweek:
        raise ValueError("gameweek range is invalid")
    consensus_by_fixture = {row.fixture_id: row for row in consensus}
    fixtures_by_team_gameweek: dict[tuple[int, int], list[CurrentFixture]] = {}
    for fixture in fixtures:
        if fixture.gameweek is None or not start_gameweek <= fixture.gameweek <= end_gameweek:
            continue
        fixtures_by_team_gameweek.setdefault((fixture.home_team_id, fixture.gameweek), []).append(fixture)
        fixtures_by_team_gameweek.setdefault((fixture.away_team_id, fixture.gameweek), []).append(fixture)
    projections: list[OddsAdjustedGameweekProjection] = []
    for player in players:
        base = xg_xa_blend(player).projected_points
        for gameweek in range(start_gameweek, end_gameweek + 1):
            player_fixtures = fixtures_by_team_gameweek.get((player.club_id, gameweek), [])
            total = 0.0
            odds_backed = 0
            for fixture in player_fixtures:
                difficulty = fixture.home_difficulty if player.club_id == fixture.home_team_id else fixture.away_difficulty
                multiplier = DIFFICULTY_MULTIPLIERS[difficulty]
                market = consensus_by_fixture.get(fixture.id)
                if market is not None:
                    win_probability = market.home_win_probability if player.club_id == fixture.home_team_id else market.away_win_probability
                    multiplier *= 1 + ODDS_WIN_WEIGHT * (win_probability - 0.5)
                    odds_backed += 1
                total += base * multiplier
            projections.append(OddsAdjustedGameweekProjection(
                player_id=player.id, player_name=player.name, position=player.position, club=player.club, cost=player.cost,
                gameweek=gameweek, fixture_count=len(player_fixtures), odds_backed_fixture_count=odds_backed,
                projected_points=round(total, 4),
            ))
    return projections


class OddsProjectionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build(self, start_gameweek: int, end_gameweek: int) -> OddsProjectionCatalog:
        projections = build_odds_adjusted_projections(
            CurrentPlayerCatalogStore(self.root).latest_players(), CurrentFixtureCatalogStore(self.root).latest(),
            FixtureOddsConsensusStore(self.root).latest(), start_gameweek, end_gameweek,
        )
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = self.root / "normalized" / "current" / "odds_projections" / f"gw{start_gameweek}-{end_gameweek}.{run_id}.{ODDS_PROJECTION_METHOD}.jsonl"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in projections), encoding="utf-8")
        return OddsProjectionCatalog(start_gameweek, end_gameweek, len(projections), str(output_path), created_at)
