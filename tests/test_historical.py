from aifpl.historical import HistoricalSourceError, HistoricalSeasonImporter, parse_gameweek_csv


CSV = """element,name,position,team,kickoff_time,fixture,opponent_team,was_home,minutes,total_points,goals_scored,assists,clean_sheets,saves,bonus,value\n1,Test Player,MID,Test FC,2025-08-15T19:00:00Z,1,2,True,90,10,1,1,0,0,3,75\n"""


def test_parse_gameweek_csv_normalizes_result_record() -> None:
    records = parse_gameweek_csv("2025-26", 1, CSV)

    assert len(records) == 1
    assert records[0].player_id == 1
    assert records[0].was_home is True
    assert records[0].total_points == 10


def test_parse_gameweek_csv_requires_expected_columns() -> None:
    try:
        parse_gameweek_csv("2025-26", 1, "name,total_points\nPlayer,5\n")
    except HistoricalSourceError as exc:
        assert "required FPL result columns" in str(exc)
    else:
        raise AssertionError("Expected a source validation error")


def test_importer_writes_raw_normalized_data_and_manifest(tmp_path, monkeypatch) -> None:
    importer = HistoricalSeasonImporter(tmp_path, source_base_url="https://example.test/data")
    monkeypatch.setattr(importer, "_download", lambda _: CSV)

    summary = importer.import_season("2025-26", 1, 2)
    loaded = importer.summary("2025-26")

    assert summary.records == 2
    assert loaded == summary
    assert (tmp_path / f"raw/historical/vaastav/2025-26/{summary.import_id}/gws/gw1.csv").exists()
    assert (tmp_path / summary.normalized_path).read_text().count("\n") == 2


def test_repeated_imports_keep_their_own_immutable_files(tmp_path, monkeypatch) -> None:
    importer = HistoricalSeasonImporter(tmp_path, source_base_url="https://example.test/data")
    monkeypatch.setattr(importer, "_download", lambda _: CSV)

    first = importer.import_season("2025-26", 1, 1)
    second = importer.import_season("2025-26", 1, 1)

    assert first.import_id != second.import_id
    assert (tmp_path / first.normalized_path).exists()
    assert (tmp_path / second.normalized_path).exists()
