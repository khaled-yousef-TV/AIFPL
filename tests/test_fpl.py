from datetime import datetime, timezone

from aifpl.fpl import summarize_bootstrap, summarize_event_live, summarize_fixtures


def test_summarize_bootstrap_identifies_current_and_next_event() -> None:
    payload = {
        "elements": [{"id": 1}, {"id": 2}],
        "teams": [{"id": 1}],
        "events": [{"id": 1, "is_current": True}, {"id": 2, "is_next": True}],
    }

    summary = summarize_bootstrap(payload, datetime(2026, 8, 7, tzinfo=timezone.utc))

    assert summary.players == 2
    assert summary.teams == 1
    assert summary.current_event == 1
    assert summary.next_event == 2


def test_summarize_fixture_and_event_data() -> None:
    fetched_at = datetime(2026, 8, 7, tzinfo=timezone.utc)
    fixtures = summarize_fixtures(
        [{"event": 1, "finished": True}, {"event": 2, "finished": False}], fetched_at
    )
    event = summarize_event_live(
        1, {"elements": [{"stats": {"total_points": 6}}, {"stats": {"total_points": 2}}]}, fetched_at
    )

    assert fixtures.finished == 1
    assert fixtures.gameweeks == [1, 2]
    assert event.players == 2
    assert event.total_points == 8
