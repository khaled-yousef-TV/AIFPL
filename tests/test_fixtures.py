from aifpl.fixtures import normalize_fixtures


def test_normalize_fixtures_preserves_official_team_difficulties() -> None:
    fixtures = normalize_fixtures([{
        "id": 1, "event": 2, "kickoff_time": "2026-08-21T19:00:00Z", "team_h": 1, "team_a": 2,
        "team_h_difficulty": 2, "team_a_difficulty": 4, "finished": False,
    }])

    assert fixtures[0].gameweek == 2
    assert fixtures[0].home_difficulty == 2
    assert fixtures[0].away_difficulty == 4
