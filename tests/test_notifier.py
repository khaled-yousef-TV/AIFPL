from __future__ import annotations

import json
from datetime import datetime, time, timezone

import httpx
import pytest

from aifpl.config import SchedulerSettings
from aifpl.hermes import HermesDecision, HermesSquadState, HermesStrategy
from aifpl.notifier import TelegramNotifier, TelegramNotifierError, build_recommendation_message, recommend_chip
from aifpl.scheduler import DeadlineScheduler
from aifpl.snapshots import SnapshotStore


def strategy() -> HermesStrategy:
    return HermesStrategy(
        risk_tolerance=0.4, hit_aversion=0.8, differential_appetite=0.3,
        planning_horizon=4, preferred_players=[], rationale="test",
    )


def decision(*, transfers_in: list[int] = [], transfers_out: list[int] = [],
             action: str = "hold") -> HermesDecision:
    squad = HermesSquadState(
        player_ids=list(range(1, 16)), bank=20, free_transfers=1,
        purchase_prices={element: 50 for element in range(1, 16)},
    )
    return HermesDecision(
        action=action, gameweek=1, squad=squad, captain_id=1, starting_xi_ids=list(range(1, 12)),
        transfers_out=transfers_out, transfers_in=transfers_in, explanation="test",
        strategy=strategy(), model="deepseek-v4-flash",
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        backend_methodology="test", decision_path="d.json", state_path="s.json", season_id="2026-27",
    )


def test_recommend_chip_suggests_wildcard_for_large_transfer_plan() -> None:
    advice = recommend_chip(decision(transfers_in=[16, 17, 18], transfers_out=[3, 4, 5], action="execute_horizon"), 0)

    assert advice is not None
    assert advice.chip == "wildcard"


def test_recommend_chip_suggests_bench_boost_for_strong_bench() -> None:
    advice = recommend_chip(decision(), bench_projected_points=13.4)

    assert advice is not None
    assert advice.chip == "bench_boost"


def test_recommend_chip_is_silent_without_a_reason() -> None:
    assert recommend_chip(decision(), bench_projected_points=5) is None


def test_telegram_send_posts_to_bot_api() -> None:
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    notifier = TelegramNotifier("bot-token", "chat-1", transport=httpx.MockTransport(handler))
    notifier.send_message("hello")

    assert recorded[0].url.path == "/botbot-token/sendMessage"
    assert json.loads(recorded[0].content)["chat_id"] == "chat-1"
    assert json.loads(recorded[0].content)["text"] == "hello"


def test_telegram_send_raises_on_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "chat not found"})

    notifier = TelegramNotifier("bot-token", "chat-1", transport=httpx.MockTransport(handler))

    with pytest.raises(TelegramNotifierError, match="chat not found"):
        notifier.send_message("hello")


def test_message_builds_without_data(tmp_path) -> None:
    message = build_recommendation_message(tmp_path, 1, "2026-27", datetime(2026, 8, 14, 18, tzinfo=timezone.utc))

    assert "GW 1 | 2026-27" in message
    assert "No Hermes decision exists yet" in message


def test_recommendation_message_includes_the_committed_plan(tmp_path) -> None:
    from aifpl.artifacts import json_bytes, write_immutable
    from aifpl.hermes import HermesState, HorizonPlanSnapshot, HorizonPlanWeekSnapshot

    plan = HorizonPlanSnapshot(
        projection_catalog="gw1-2.x.jsonl", pre_season=True, solver_status="OPTIMAL",
        methodology="test", total_projected_points=120.0, total_hit_cost=4,
        total_net_projected_points=116.0, robustness_score=70.0,
        weeks=[
            HorizonPlanWeekSnapshot(gameweek=1, transfers_made=0, free_transfers_before=5, hit_cost=0,
                                    bank_after=250, projected_points=60.0, net_projected_points=60.0,
                                    odds_coverage=1.0, unlimited_transfers=True, free_transfers_after=1),
            HorizonPlanWeekSnapshot(gameweek=2, transfers_made=1, free_transfers_before=1, hit_cost=0,
                                    bank_after=250, projected_points=60.0, net_projected_points=60.0,
                                    odds_coverage=1.0, free_transfers_after=1,
                                    outgoing_ids=[3], incoming_ids=[16], captain_id=16),
        ],
    )
    record = decision(transfers_in=[16], transfers_out=[3], action="execute_horizon").model_copy(
        update={"horizon_plan": plan},
    )
    stamp = "20260814T100000000000Z"
    write_immutable(
        tmp_path / "hermes" / "decisions" / f"{stamp}.json",
        json_bytes(record.model_dump(mode="json"), pretty=True),
    )
    state = HermesState(
        strategy=record.strategy, squad=record.squad, captain_id=1,
        starting_xi_ids=list(range(1, 12)), model="test",
        updated_at=datetime(2026, 8, 14, tzinfo=timezone.utc), version=1,
        gameweek=1, season_id="2026-27",
    )
    write_immutable(
        tmp_path / "hermes" / "states" / f"{stamp}.json",
        json_bytes(state.model_dump(mode="json"), pretty=True),
    )

    message = build_recommendation_message(tmp_path, 1, "2026-27", datetime(2026, 8, 14, 18, tzinfo=timezone.utc))

    assert "Plan: 116.0 net pts | 4 pts hits | robustness 70" in message
    assert "GW1 | unlimited transfers | 1 FT after" in message
    assert "GW2 | 1 transfer(s) | 1 FT after" in message


def test_scheduler_notifies_once_within_lead_time(tmp_path, monkeypatch) -> None:
    from aifpl import notifier as notifier_module

    class FakeRefreshJob:
        def run(self, start: int, end: int, budget: int):
            raise AssertionError("refresh should not run for not_due tick")

    sent: list[str] = []

    class FakeNotifier:
        def __init__(self) -> None:
            pass

        @classmethod
        def from_environment(cls) -> "FakeNotifier":
            return cls()

        def send_message(self, text: str) -> None:
            sent.append(text)

    monkeypatch.setenv("AIFPL_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("AIFPL_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("AIFPL_TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setattr(notifier_module, "TelegramNotifier", FakeNotifier)
    bootstrap = {"elements": [], "teams": [], "events": [{"id": 1, "deadline_time": "2026-08-14T18:00:00Z"}]}
    SnapshotStore(tmp_path).save_bootstrap(bootstrap, datetime(2026, 8, 1, tzinfo=timezone.utc))
    subject = DeadlineScheduler(
        tmp_path, refresh_job=FakeRefreshJob(), settings=SchedulerSettings(90, 6, 300, 1000, time(17)),
    )

    first = subject.tick(datetime(2026, 8, 14, 15, tzinfo=timezone.utc))
    second = subject.tick(datetime(2026, 8, 14, 15, 30, tzinfo=timezone.utc))

    assert first.status == "not_due"
    assert first.telegram_notified is True
    assert second.telegram_notified is True
    assert len(sent) == 1
    assert (tmp_path / "scheduler" / "telegram_notified" / "2026-27" / "gw1.json").exists()


def test_scheduler_does_not_notify_outside_lead_window(tmp_path, monkeypatch) -> None:
    class FakeRefreshJob:
        def run(self, start: int, end: int, budget: int):
            raise AssertionError("refresh should not run for not_due tick")

    class FakeNotifier:
        @classmethod
        def from_environment(cls) -> "FakeNotifier":
            return cls()

        def send_message(self, text: str) -> None:
            raise AssertionError("should not send outside the window")

    monkeypatch.setenv("AIFPL_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("AIFPL_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("AIFPL_TELEGRAM_CHAT_ID", "chat-1")
    from aifpl import notifier as notifier_module

    monkeypatch.setattr(notifier_module, "TelegramNotifier", FakeNotifier)
    bootstrap = {"elements": [], "teams": [], "events": [{"id": 1, "deadline_time": "2026-08-14T18:00:00Z"}]}
    SnapshotStore(tmp_path).save_bootstrap(bootstrap, datetime(2026, 8, 1, tzinfo=timezone.utc))
    subject = DeadlineScheduler(
        tmp_path, refresh_job=FakeRefreshJob(), settings=SchedulerSettings(90, 6, 300, 1000, time(17)),
    )

    result = subject.tick(datetime(2026, 8, 14, 9, tzinfo=timezone.utc))

    assert result.status == "not_due"
    assert result.telegram_notified is False
