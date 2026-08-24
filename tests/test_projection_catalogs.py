import pytest

from aifpl.current_projections import CurrentPlayerProjection, CurrentProjectionStore
from aifpl.fixture_projections import FixtureGameweekProjection
from aifpl.odds_projections import OddsAdjustedGameweekProjection
from aifpl.projection_catalogs import ProjectionSource, _aggregate, _latest_ownership, load_projection_candidates
from aifpl.xg_projections import XgXaProjection


def row(gameweek: int, points: float) -> FixtureGameweekProjection:
    return FixtureGameweekProjection(1, "Player", "MID", "Arsenal", 75, gameweek, 1, 3.0, points)


def test_projection_catalog_aggregates_gameweek_rows_for_optimization() -> None:
    candidate = _aggregate([row(1, 4.5), row(2, 5.5)])[0]

    assert candidate.player_id == 1
    assert candidate.projected_points == 10


def test_projection_catalog_aggregation_fills_ownership_from_the_player_catalog() -> None:
    candidate = _aggregate([row(1, 4.5), row(2, 5.5)], {1: 32.5})[0]

    assert candidate.selected_by_percent == 32.5


def test_projection_catalog_rejects_duplicate_player_gameweek() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        _aggregate([row(1, 4), row(1, 5)])


def test_projection_catalog_rejects_incomplete_player_horizon() -> None:
    first = row(1, 4)
    second_player = FixtureGameweekProjection(2, "Other", "MID", "Chelsea", 75, 1, 1, 3.0, 4)
    second_week = FixtureGameweekProjection(1, "Player", "MID", "Arsenal", 75, 2, 1, 3.0, 5)

    with pytest.raises(ValueError, match="Incomplete"):
        _aggregate([first, second_week, second_player])


def test_xg_xa_source_carries_ownership_from_the_player_catalog(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("aifpl.projection_catalogs.CurrentProjectionStore", lambda root: FakeStore())
    monkeypatch.setattr("aifpl.projection_catalogs.CurrentPlayerCatalogStore", lambda root: FakeCatalogStore())
    monkeypatch.setattr("aifpl.projection_catalogs.XgXaProjectionStore", lambda root: FakeXgStore())

    candidate = load_projection_candidates(tmp_path, ProjectionSource.XG_XA)[0]

    assert candidate.selected_by_percent == 41.0


def test_odds_source_uses_calibrated_rows(tmp_path, monkeypatch) -> None:
    rows = [
        OddsAdjustedGameweekProjection(1, "Player", "MID", "Arsenal", 75, 1, 1, 1, 6.5, methodology="test.calibrated"),
        OddsAdjustedGameweekProjection(1, "Player", "MID", "Arsenal", 75, 2, 1, 1, 5.5, methodology="test.calibrated"),
    ]
    monkeypatch.setattr("aifpl.projection_catalogs.calibrated_odds_rows", lambda root, catalog_id: (rows, None))

    candidate = load_projection_candidates(tmp_path, ProjectionSource.ODDS, "catalog.jsonl")[0]

    assert candidate.projected_points == 12.0
    assert candidate.methodology == "test.calibrated"


class FakeXgStore:
    def latest(self):
        return [
            XgXaProjection(1, "Player", "MID", "Arsenal", 75, 80.0, 0.5, 0.4, 0.9, 0.5, 6.0, 6.5),
            XgXaProjection(2, "Other", "DEF", "Chelsea", 60, 90.0, 0.1, 0.1, 0.2, 0.3, 2.0, 3.0),
        ]


class FakeCatalogStore:
    def latest_players(self):
        return [_player(1, "Player", 41.0), _player(2, "Other", 7.5)]


class FakeStore:
    def latest(self):
        return [
            CurrentPlayerProjection(1, "Player", "MID", "Arsenal", 75, 6.5, 1.0, "test"),
            CurrentPlayerProjection(2, "Other", "DEF", "Chelsea", 60, 3.0, 1.0, "test"),
        ]


def _player(player_id: int, name: str, ownership: float):
    from aifpl.current import CurrentPlayer

    return CurrentPlayer(
        player_id, name, "MID" if player_id == 1 else "DEF", 1 if player_id == 1 else 2,
        "Arsenal" if player_id == 1 else "Chelsea", 75 if player_id == 1 else 60, "a", None,
        0, 5, 100, 1900, 25, 5, 4, 9, 20, selected_by_percent=ownership,
    )


def test_latest_ownership_reads_the_player_catalog(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("aifpl.projection_catalogs.CurrentPlayerCatalogStore", lambda root: FakeCatalogStore())

    ownership = _latest_ownership(tmp_path)

    assert ownership == {1: 41.0, 2: 7.5}
