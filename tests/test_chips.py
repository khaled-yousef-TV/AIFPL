from datetime import datetime, timezone

import pytest

from aifpl.chips import (
    CHIP_NAMES,
    ChipAdvisor,
    ChipIntel,
    ChipIntelFetcher,
    ChipState,
    ChipStateStore,
    ChipSlot,
    detect_schedule,
)
from aifpl.config import ChipSettings
from aifpl.fixtures import CurrentFixture
from aifpl.odds_projections import OddsAdjustedGameweekProjection


def settings(**overrides) -> ChipSettings:
    values = {
        "set1_end_gw": 19,
        "wildcard_points_gap": 20.0,
        "bench_boost_bench_points": 24.0,
        "tc_captain_points": 13.0,
        "tc_margin": 3.0,
        "fh_starters_without_fixture": 3,
        "intel_cache_hours": 6.0,
        "intel_max_timing": 12,
        "reddit_limit": 10,
        "rules_url": "https://example.com/rules",
        "intel_enabled": True,
    }
    values.update(overrides)
    return ChipSettings(**values)


def fresh_state(season_id: str = "2026-27") -> ChipState:
    return ChipState(
        season_id=season_id,
        slots=[ChipSlot(chip=chip, set=chip_set) for chip in CHIP_NAMES for chip_set in (1, 2)],
        updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def fixtures() -> list[CurrentFixture]:
    rows = []
    for gw in (3, 4, 5):
        for index in range(10):
            rows.append(CurrentFixture(
                id=gw * 100 + index, gameweek=gw, kickoff_time=None,
                home_team_id=index * 2 + 1, away_team_id=index * 2 + 2,
                home_difficulty=2, away_difficulty=2, finished=False,
            ))
    rows.extend([
        CurrentFixture(id=6200, gameweek=6, kickoff_time=None, home_team_id=1, away_team_id=2, home_difficulty=2, away_difficulty=2, finished=False),
        CurrentFixture(id=6201, gameweek=6, kickoff_time=None, home_team_id=3, away_team_id=4, home_difficulty=2, away_difficulty=2, finished=False),
        CurrentFixture(id=6202, gameweek=6, kickoff_time=None, home_team_id=1, away_team_id=5, home_difficulty=2, away_difficulty=2, finished=False),
        CurrentFixture(id=6203, gameweek=6, kickoff_time=None, home_team_id=6, away_team_id=7, home_difficulty=2, away_difficulty=2, finished=False),
    ])
    return rows


def projection(player_id: int, gameweek: int, points: float) -> OddsAdjustedGameweekProjection:
    return OddsAdjustedGameweekProjection(
        player_id, f"Player {player_id}", "MID", "Club", 50, gameweek, 1, 1, points,
    )


def projections_for(players: list[int], gameweeks: list[int], points: float = 5.0) -> list[OddsAdjustedGameweekProjection]:
    return [projection(player_id, gw, points) for player_id in players for gw in gameweeks]


def test_detect_schedule_finds_double_and_blank_gameweeks() -> None:
    schedule = detect_schedule(fixtures())

    assert schedule[6]["double"] is True
    assert schedule[3]["double"] is False
    assert schedule[6]["blank"] is True
    assert schedule[3]["blank"] is False
    assert schedule[6]["teams_with_two_fixtures"] == 1


def test_detect_schedule_merges_expected_windows() -> None:
    schedule = detect_schedule(fixtures(), expected_dgw=[22], expected_bgw=[24])

    assert schedule[22]["double"] is True
    assert schedule[22]["expected"] is True
    assert schedule[24]["blank"] is True


def test_advisor_recommends_wildcard_when_gap_is_large() -> None:
    advisor = ChipAdvisor(settings())
    rows = projections_for(list(range(1, 16)), [3, 4, 5, 6], points=5.0) + projections_for(
        list(range(21, 36)), [3, 4, 5, 6], points=8.0,
    )
    advice = advisor.evaluate(
        "2026-27", 3, fresh_state(), fixtures(), rows,
        list(range(1, 16)), list(range(1, 12)), list(range(21, 36)),
        ChipIntel(fetched_at=datetime.now(timezone.utc)),
    )
    wildcard = next(item for item in advice.recommendations if item.chip == "wildcard" and item.set == 1)

    assert wildcard.status == "recommend"
    assert wildcard.gameweek == 3


def test_advisor_saves_wildcard_when_gap_is_small() -> None:
    advisor = ChipAdvisor(settings())
    rows = projections_for(list(range(1, 16)), [3, 4, 5, 6], points=5.0) + projections_for(
        list(range(21, 36)), [3, 4, 5, 6], points=5.15,
    )
    advice = advisor.evaluate(
        "2026-27", 3, fresh_state(), fixtures(), rows,
        list(range(1, 16)), list(range(1, 12)), list(range(21, 36)),
        ChipIntel(fetched_at=datetime.now(timezone.utc)),
    )
    wildcard = next(item for item in advice.recommendations if item.chip == "wildcard" and item.set == 1)

    assert wildcard.status == "save"
    assert wildcard.conditions["projected_gap"] < 20


def test_advisor_marks_used_slots_and_expired_set_one() -> None:
    state = fresh_state()
    state = state.model_copy(update={
        "slots": [
            slot.model_copy(update={"used": True, "used_gw": 2})
            if slot.chip == "wildcard" and slot.set == 1 else slot
            for slot in state.slots
        ]
    })
    advisor = ChipAdvisor(settings())
    advice = advisor.evaluate(
        "2026-27", 21, state, fixtures(), projections_for(list(range(1, 16)), [21, 22], points=5.0),
        list(range(1, 16)), list(range(1, 12)), list(range(1, 16)),
        ChipIntel(fetched_at=datetime.now(timezone.utc), rules={"set1_end_gw": 19}),
    )
    by_key = {(item.chip, item.set): item for item in advice.recommendations}

    assert by_key[("wildcard", 1)].status == "used"
    assert by_key[("bench_boost", 1)].status == "expired"
    assert by_key[("wildcard", 2)].status in ("recommend", "save")


def test_advisor_recommends_bench_boost_on_strong_double_gameweek_bench() -> None:
    advisor = ChipAdvisor(settings(bench_boost_bench_points=24.0))
    rows = projections_for(list(range(1, 16)), [3, 4, 5], points=5.0)
    rows += [projection(player_id, 6, 12.0) for player_id in range(1, 16)]
    advice = advisor.evaluate(
        "2026-27", 3, fresh_state(), fixtures(), rows,
        list(range(1, 16)), list(range(1, 12)), list(range(1, 16)),
        ChipIntel(fetched_at=datetime.now(timezone.utc)),
    )
    bb = next(item for item in advice.recommendations if item.chip == "bench_boost" and item.set == 1)

    assert bb.status == "recommend"
    assert bb.gameweek == 6


def test_advisor_recommends_triple_captain_on_double_gameweek() -> None:
    advisor = ChipAdvisor(settings(tc_captain_points=13.0, tc_margin=3.0))
    rows = projections_for(list(range(1, 16)), [3, 4, 5], points=5.0)
    rows += [projection(player_id, 6, 16.0 if player_id == 1 else 8.0) for player_id in range(1, 16)]
    advice = advisor.evaluate(
        "2026-27", 3, fresh_state(), fixtures(), rows,
        list(range(1, 16)), list(range(1, 12)), list(range(1, 16)),
        ChipIntel(fetched_at=datetime.now(timezone.utc)),
    )
    tc = next(item for item in advice.recommendations if item.chip == "triple_captain" and item.set == 1)

    assert tc.status == "recommend"
    assert tc.gameweek == 6


def test_advisor_saves_triple_captain_without_margin() -> None:
    advisor = ChipAdvisor(settings(tc_captain_points=13.0, tc_margin=3.0))
    rows = projections_for(list(range(1, 16)), [3, 4, 5], points=5.0)
    rows += [projection(player_id, 6, 14.0 if player_id == 1 else 13.5) for player_id in range(1, 16)]
    advice = advisor.evaluate(
        "2026-27", 3, fresh_state(), fixtures(), rows,
        list(range(1, 16)), list(range(1, 12)), list(range(1, 16)),
        ChipIntel(fetched_at=datetime.now(timezone.utc)),
    )
    tc = next(item for item in advice.recommendations if item.chip == "triple_captain" and item.set == 1)

    assert tc.status == "save"


def test_state_store_marks_used_immutably(tmp_path) -> None:
    store = ChipStateStore(tmp_path)
    initial = store.latest("2026-27")

    updated = store.mark_used("2026-27", "bench_boost", 1, 5)

    slot = next(slot for slot in updated.slots if slot.chip == "bench_boost" and slot.set == 1)
    assert slot.used is True
    assert slot.used_gw == 5
    assert store.latest("2026-27") == updated
    assert len([
        path for path in (tmp_path / "chips" / "state" / "2026-27").glob("*.json")
        if not path.name.endswith(".manifest.json")
    ]) == 1


def test_state_store_validates_chip_names(tmp_path) -> None:
    with pytest.raises(ValueError, match="chip must be one of"):
        ChipStateStore(tmp_path).mark_used("2026-27", "wildcardd", 1, 5)


class FakeResponse:
    def __init__(self, text: str = "", payload: object | None = None) -> None:
        self._text = text
        self._payload = payload if payload is not None else {"data": {"children": []}}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload

    @property
    def text(self) -> str:
        return self._text


def test_intel_fetcher_reuses_fresh_cache(tmp_path, monkeypatch) -> None:
    calls = {"count": 0}

    class FakeClient:
        def get(self, url, params=None):
            calls["count"] += 1
            if "search.json" in url:
                return FakeResponse(payload={"data": {"children": []}})
            return FakeResponse(text="eight chips")

    fetcher = ChipIntelFetcher(tmp_path, settings=settings(), client=FakeClient())
    first = fetcher.fetch(3)
    second = fetcher.fetch(3)

    assert calls["count"] == 5  # one rules fetch + four chip searches
    assert first.rules.get("chips_per_set") == 2
    assert second == first


def test_intel_fetcher_falls_back_to_cached_on_failure(tmp_path, monkeypatch) -> None:
    from datetime import timedelta

    from aifpl.artifacts import json_bytes, write_immutable

    old = ChipIntel(
        fetched_at=datetime.now(timezone.utc) - timedelta(hours=48),
        rules={"chips_per_set": 2, "set1_end_gw": 19},
    )
    path = tmp_path / "chips" / "intel" / "20260801T000000000000Z.json"
    write_immutable(path, json_bytes(old.model_dump(mode="json"), pretty=True))

    class BrokenClient:
        def get(self, url, params=None):
            raise RuntimeError("down")

    intel = ChipIntelFetcher(tmp_path, settings=settings(), client=BrokenClient()).fetch(3)

    assert intel.rules == old.rules
    assert intel.stale is True


def test_intel_fetcher_parses_expected_windows_and_timing(tmp_path) -> None:
    class FakeClient:
        def get(self, url, params=None):
            if "search.json" in url:
                return FakeResponse(payload={"data": {"children": [
                    {"data": {"title": "Bench boost GW22 double gameweek", "selftext": "Use it in GW22"}}
                ]}})
            return FakeResponse(text="eight chips, first set expires at Gameweek 19 deadline")

    intel = ChipIntelFetcher(tmp_path, settings=settings(), client=FakeClient()).fetch(3)

    assert 22 in intel.expected_dgw_gws
    assert intel.rules["set1_end_gw"] == 19
    assert any(item.chip == "bench_boost" and item.gameweek == 22 for item in intel.timing)
