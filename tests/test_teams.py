from aifpl.teams import normalize_teams


def test_normalize_teams_returns_logo_urls_by_team_id() -> None:
    teams = normalize_teams({
        "teams": [
            {"id": 2, "name": "Aston Villa", "short_name": "AVL", "code": 7},
            {"id": 1, "name": "Arsenal", "short_name": "ARS", "code": 3},
        ],
    })

    assert [team.id for team in teams] == [1, 2]
    assert teams[0].logo_url == "/teams/1/logo.png"


def test_normalize_teams_rejects_missing_required_fields() -> None:
    try:
        normalize_teams({"teams": [{"id": 1, "name": "Arsenal"}]})
    except ValueError as exc:
        assert "required FPL fields" in str(exc)
    else:
        raise AssertionError("normalize_teams should reject incomplete team records")
