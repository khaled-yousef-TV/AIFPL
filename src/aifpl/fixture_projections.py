from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aifpl.current import CurrentPlayer
from aifpl.current_projections import CurrentPlayerProjection, fpl_source_baseline
from aifpl.fixtures import CurrentFixture


DIFFICULTY_MULTIPLIERS = {1: 1.15, 2: 1.075, 3: 1.0, 4: 0.925, 5: 0.85}
FIXTURE_PROJECTION_METHOD = "fpl_source_baseline_v1.fixture_difficulty_v1"


@dataclass(frozen=True)
class FixtureGameweekProjection:
    player_id: int
    player_name: str
    position: str
    club: str
    cost: int
    gameweek: int
    fixture_count: int
    average_difficulty: float | None
    projected_points: float
    methodology: str = FIXTURE_PROJECTION_METHOD


@dataclass(frozen=True)
class FixtureProjectionCatalog:
    start_gameweek: int
    end_gameweek: int
    records: int
    output_path: str
    created_at: datetime


def build_fixture_gameweek_projections(
    players: list[CurrentPlayer], fixtures: list[CurrentFixture], start_gameweek: int, end_gameweek: int
) -> list[FixtureGameweekProjection]:
    if start_gameweek < 1 or end_gameweek < start_gameweek:
        raise ValueError("gameweek range is invalid")
    by_team_gameweek: dict[tuple[int, int], list[int]] = {}
    for fixture in fixtures:
        if fixture.gameweek is None or not start_gameweek <= fixture.gameweek <= end_gameweek:
            continue
        by_team_gameweek.setdefault((fixture.home_team_id, fixture.gameweek), []).append(fixture.home_difficulty)
        by_team_gameweek.setdefault((fixture.away_team_id, fixture.gameweek), []).append(fixture.away_difficulty)
    projections: list[FixtureGameweekProjection] = []
    for player in players:
        baseline = fpl_source_baseline(player)
        for gameweek in range(start_gameweek, end_gameweek + 1):
            difficulties = by_team_gameweek.get((player.club_id, gameweek), [])
            multipliers = [DIFFICULTY_MULTIPLIERS[difficulty] for difficulty in difficulties]
            projections.append(FixtureGameweekProjection(
                player_id=player.id,
                player_name=player.name,
                position=player.position,
                club=player.club,
                cost=player.cost,
                gameweek=gameweek,
                fixture_count=len(difficulties),
                average_difficulty=round(sum(difficulties) / len(difficulties), 4) if difficulties else None,
                projected_points=round(baseline.projected_points * sum(multipliers), 4),
            ))
    return projections


class FixtureProjectionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build(self, start_gameweek: int, end_gameweek: int) -> FixtureProjectionCatalog:
        from aifpl.current import CurrentPlayerCatalogStore
        from aifpl.fixtures import CurrentFixtureCatalogStore

        projections = build_fixture_gameweek_projections(
            CurrentPlayerCatalogStore(self.root).latest_players(), CurrentFixtureCatalogStore(self.root).latest(),
            start_gameweek, end_gameweek,
        )
        created_at = datetime.now(timezone.utc)
        run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = self.root / "normalized" / "current" / "fixture_projections" / f"gw{start_gameweek}-{end_gameweek}.{run_id}.{FIXTURE_PROJECTION_METHOD}.jsonl"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in projections), encoding="utf-8")
        return FixtureProjectionCatalog(start_gameweek, end_gameweek, len(projections), str(output_path), created_at)
