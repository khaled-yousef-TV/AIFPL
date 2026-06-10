"""Unit tests for mechanics agent DGW/BGW detection."""

from types import SimpleNamespace


from agents.mechanics_agent import detect_fixture_load


def fx(event, team_h, team_a):
    return SimpleNamespace(event=event, team_h=team_h, team_a=team_a)


TEAMS = {1: "ARS", 2: "LIV", 3: "MCI", 4: "CHE"}


def test_normal_gameweek_has_no_load_entry():
    fixtures = [fx(10, 1, 2), fx(10, 3, 4)]
    load = detect_fixture_load([10], fixtures, TEAMS)
    assert load == []


def test_double_gameweek_detected():
    # ARS and LIV play twice in GW11; MCI/CHE blank
    fixtures = [fx(11, 1, 2), fx(11, 2, 1)]
    load = detect_fixture_load([11], fixtures, TEAMS)
    assert len(load) == 1
    entry = load[0]
    assert entry.gameweek == 11
    assert entry.double_teams == ["ARS", "LIV"]
    assert entry.blank_teams == ["CHE", "MCI"]


def test_blank_gameweek_detected():
    # Only one fixture scheduled: two teams blank
    fixtures = [fx(12, 1, 2)]
    load = detect_fixture_load([12], fixtures, TEAMS)
    assert len(load) == 1
    assert load[0].double_teams == []
    assert load[0].blank_teams == ["CHE", "MCI"]


def test_unscheduled_gameweek_skipped():
    # GW with zero fixtures scheduled can't be classified -> skipped entirely
    fixtures = [fx(10, 1, 2), fx(10, 3, 4)]
    load = detect_fixture_load([10, 38], fixtures, TEAMS)
    assert load == []


def test_fixtures_with_null_event_ignored():
    fixtures = [fx(None, 1, 2), fx(10, 1, 2), fx(10, 3, 4)]
    load = detect_fixture_load([10], fixtures, TEAMS)
    assert load == []
