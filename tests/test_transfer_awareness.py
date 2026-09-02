import json
from pathlib import Path

from aifpl.current import CurrentPlayer
from aifpl.transfer_awareness import TransferAwarenessStore, TransferProfile, normalize_club_name
from aifpl.xg_projections import xg_xa_blend


def make_player(player_id: int, club: str, minutes: int = 0, starts: int = 0) -> CurrentPlayer:
    return CurrentPlayer(
        id=player_id, name=f"Player {player_id}", position="MID", club_id=1, club=club,
        cost=70, status="a", chance_of_playing_next_round=100, form=0.0,
        points_per_game=0.0, total_points=0, minutes=minutes, starts=starts,
        expected_goals=0.0, expected_assists=0.0, expected_goal_involvements=0.0,
        expected_goals_conceded=0.0, first_name=f"Test{player_id}", second_name="Player",
    )


def write_previous_season(root: Path, rows: list[dict]) -> None:
    season_dir = root / "normalized" / "historical" / "2025-26"
    imports = season_dir / "imports"
    imports.mkdir(parents=True, exist_ok=True)
    normalized = season_dir / "import-1" / "player_gameweeks.jsonl"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    (imports / "import-1.json").write_text(json.dumps({
        "season": "2025-26", "import_id": "import-1",
        "imported_at": "2026-07-01T00:00:00Z",
        "normalized_path": str(normalized),
    }), encoding="utf-8")


def gw_row(player_id: int, team: str, gameweek: int, minutes: int, goals: int = 0, assists: int = 0, points: int = 0) -> dict:
    return {
        "season": "2025-26", "gameweek": gameweek, "player_id": player_id,
        "player_name": f"Test{player_id} Player", "position": "MID", "team": team,
        "kickoff_time": "2026-01-01T15:00:00Z", "fixture_id": 1,
        "opponent_team_id": 2, "was_home": True, "minutes": minutes,
        "total_points": points, "goals_scored": goals, "assists": assists,
        "clean_sheets": 0, "saves": 0, "bonus": 0, "value": 70,
    }


def test_new_signing_detected_when_club_changed(tmp_path) -> None:
    write_previous_season(tmp_path, [gw_row(7, "Brighton", 38, 2700, 12, 6, 150)])
    players = [make_player(7, "Chelsea")]

    profile = TransferAwarenessStore(tmp_path).latest(players)[7]

    assert profile.is_new_signing is True
    assert profile.previous_club == "Brighton"
    assert profile.minutes_multiplier == 0.85
    assert profile.prior_goals_per_90 == 0.4


def test_previous_season_rows_aggregate_double_gameweeks_and_retain_club_history(tmp_path) -> None:
    write_previous_season(tmp_path, [
        gw_row(7, "Brighton", 1, 90, 1, 0, 10),
        gw_row(7, "Brighton", 2, 90, 0, 1, 8),
        gw_row(7, "Chelsea", 3, 90, 1, 1, 12),
        gw_row(7, "Chelsea", 3, 180, 2, 0, 20),
    ])

    profile = TransferAwarenessStore(tmp_path).latest([make_player(7, "Chelsea")])[7]

    assert profile.is_new_signing is False
    assert profile.previous_club == "Chelsea"
    assert profile.club_history == ("Brighton", "Chelsea")
    assert profile.prior_goals_per_90 == 0.8
    assert profile.prior_assists_per_90 == 0.4
    assert profile.prior_points_per_90 == 10.0


def test_same_club_is_not_a_new_signing(tmp_path) -> None:
    write_previous_season(tmp_path, [gw_row(7, "Chelsea", 38, 2700, 12, 6, 150)])
    players = [make_player(7, "Chelsea")]

    profile = TransferAwarenessStore(tmp_path).latest(players)[7]

    assert profile.is_new_signing is False
    assert profile.minutes_multiplier == 1.0


def test_missing_history_has_no_penalty(tmp_path) -> None:
    players = [make_player(9, "Chelsea")]

    profile = TransferAwarenessStore(tmp_path).latest(players)[9]

    assert profile.is_new_signing is False
    assert profile.minutes_multiplier == 1.0
    assert profile.previous_club is None


def test_club_names_are_normalized_for_comparison() -> None:
    assert normalize_club_name("Nott'm Forest") == normalize_club_name("Nottm Forest")
    assert normalize_club_name("Man Utd") != normalize_club_name("Man City")


def test_blend_uses_prior_stats_when_no_current_minutes(tmp_path) -> None:
    write_previous_season(tmp_path, [gw_row(7, "Brighton", 38, 2700, 12, 6, 150)])
    player = make_player(7, "Chelsea")
    profile = TransferAwarenessStore(tmp_path).latest([player])[7]

    projection = xg_xa_blend(player, gameweeks_elapsed=1, transfer_profile=profile)

    assert projection.xg_per_90 > 0
    assert projection.xa_per_90 > 0


def test_blend_applies_minutes_risk_multiplier(tmp_path) -> None:
    write_previous_season(tmp_path, [gw_row(7, "Brighton", 38, 2700, 12, 6, 150)])
    player = make_player(7, "Chelsea", minutes=900, starts=10)
    profile = TransferAwarenessStore(tmp_path).latest([player])[7]

    with_multiplier = xg_xa_blend(player, gameweeks_elapsed=10, transfer_profile=profile)
    without = xg_xa_blend(player, gameweeks_elapsed=10)

    assert with_multiplier.expected_minutes < without.expected_minutes
    assert profile.minutes_multiplier == 0.85


def test_transfer_profile_defaults(tmp_path) -> None:
    profile = TransferAwarenessStore(tmp_path).latest([make_player(3, "Arsenal")])[3]

    assert isinstance(profile, TransferProfile)
    assert profile.has_prior_stats is False
