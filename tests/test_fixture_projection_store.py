from datetime import datetime, timezone

from aifpl.current import CurrentPlayerCatalogStore
from aifpl.fixture_projections import FixtureProjectionStore
from aifpl.fixtures import CurrentFixtureCatalogStore
from aifpl.snapshots import SnapshotStore


def test_fixture_projection_builds_are_immutable(tmp_path) -> None:
    bootstrap = {
        "teams": [{"id": 1, "name": "Arsenal"}, {"id": 2, "name": "Chelsea"}],
        "elements": [{"id": 1, "web_name": "Test", "element_type": 3, "team": 1, "now_cost": 50,
                      "status": "a", "chance_of_playing_next_round": None, "form": "0", "points_per_game": "5", "total_points": 10,
                      "minutes": 900, "starts": 10, "expected_goals": "4", "expected_assists": "3",
                      "expected_goal_involvements": "7", "expected_goals_conceded": "8"}],
        "events": [],
    }
    fixtures = [{"id": 1, "event": 1, "kickoff_time": None, "team_h": 1, "team_a": 2,
                 "team_h_difficulty": 3, "team_a_difficulty": 3, "finished": False}]
    snapshots = SnapshotStore(tmp_path)
    snapshots.save_bootstrap(bootstrap, datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc))
    snapshots.save_fixtures(fixtures, datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc))
    CurrentPlayerCatalogStore(tmp_path).normalize_latest()
    CurrentFixtureCatalogStore(tmp_path).normalize_latest()

    first = FixtureProjectionStore(tmp_path).build(1, 1)
    second = FixtureProjectionStore(tmp_path).build(1, 1)

    assert first.output_path != second.output_path
    assert first.manifest_path is not None
    assert first.source_player_catalog is not None
    assert first.source_fixture_catalog is not None
    assert FixtureProjectionStore(tmp_path).latest()
