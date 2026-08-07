from datetime import datetime, timezone

from aifpl.current import CurrentPlayerCatalogStore, normalize_bootstrap_players
from aifpl.snapshots import SnapshotStore


def payload() -> dict:
    return {
        "teams": [{"id": 1, "name": "Arsenal"}],
        "elements": [{
            "id": 7, "web_name": "Saka", "element_type": 3, "team": 1, "now_cost": 100,
            "status": "a", "chance_of_playing_next_round": 100, "form": "4.5", "points_per_game": "5.1", "total_points": 175,
            "minutes": 2500, "starts": 28, "expected_goals": "8.5", "expected_assists": "6.2", "expected_goal_involvements": "14.7", "expected_goals_conceded": "20.0",
        }],
        "events": [],
    }


def test_normalize_bootstrap_players_preserves_real_fpl_identifiers_and_fields() -> None:
    players = normalize_bootstrap_players(payload())

    assert players[0].id == 7
    assert players[0].position == "MID"
    assert players[0].club == "Arsenal"
    assert players[0].cost == 100
    assert players[0].points_per_game == 5.1


def test_catalog_store_derives_versioned_player_catalog_from_snapshot(tmp_path) -> None:
    SnapshotStore(tmp_path).save_bootstrap(payload(), datetime(2026, 8, 7, tzinfo=timezone.utc))
    store = CurrentPlayerCatalogStore(tmp_path)

    catalog = store.normalize_latest()
    players = store.latest_players()

    assert catalog.players == 1
    assert players[0].name == "Saka"
    assert "normalized/current/players" in catalog.output_path
