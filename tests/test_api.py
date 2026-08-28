from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aifpl import api
from aifpl.dashboard import CurrentDashboard


def test_health_endpoint() -> None:
    response = TestClient(api.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_lifespan_starts_and_stops_the_scheduler(monkeypatch, tmp_path) -> None:
    started = Event()
    stopped = Event()

    class FakeScheduler:
        def __init__(self, root) -> None:
            assert root == tmp_path

        def run_forever(self, stop_event: Event) -> None:
            started.set()
            stop_event.wait(2)
            stopped.set()

    monkeypatch.setenv("AIFPL_SCHEDULER_ENABLED", "true")
    monkeypatch.setattr(api, "DeadlineScheduler", FakeScheduler)
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)

    with TestClient(api.app):
        assert started.wait(1)
    assert stopped.is_set()


def test_latest_snapshot_returns_not_found_without_data(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)

    response = TestClient(api.app).get("/snapshots/latest")

    assert response.status_code == 404
    assert "No FPL bootstrap snapshots" in response.json()["detail"]


def test_as_of_endpoint_rejects_a_naive_timestamp(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)

    response = TestClient(api.app).get("/snapshots/as-of?at=2026-08-07T12:00:00")

    assert response.status_code == 422


def test_historical_summary_is_not_found_before_import(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)

    response = TestClient(api.app).get("/historical/seasons/2025-26")

    assert response.status_code == 404


def test_current_teams_use_the_bundled_manifest_without_a_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)

    response = TestClient(api.app).get("/teams/current")

    assert response.status_code == 200
    assert len(response.json()) == 20
    assert response.json()[0]["short_name"] == "ARS"
    assert response.json()[0]["logo_url"] == "/teams/1/logo.png"


def test_current_team_logo_is_served_from_the_bundled_assets(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)

    response = TestClient(api.app).get("/teams/1/logo.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_current_dashboard_returns_the_backend_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    expected = CurrentDashboard(
        gameweek=1,
        season_id="2026-27",
        deadline=datetime(2026, 8, 21, 17, 30, tzinfo=timezone.utc),
        action="hold",
        explanation="Use the committed lineup",
        model="test-model",
        methodology="test-methodology",
        bank=10,
        free_transfers=2,
        captain_id=1,
        formation="4-4-2",
        projected_points=55.5,
        projection_available=True,
        players=[],
    )
    monkeypatch.setattr(api, "build_current_dashboard", lambda root: expected)

    response = TestClient(api.app).get("/dashboard/current")

    assert response.status_code == 200
    assert response.json()["projected_points"] == 55.5
    assert response.json()["free_transfers"] == 2


def test_horizon_plan_route_passes_preseason_and_penalty_options(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    captured: dict = {}

    monkeypatch.setattr(api, "calibrated_odds_rows", lambda root, catalog_id: ([], None))

    def fake_plan(rows, state, **kwargs):
        captured.update(kwargs)
        return api.HorizonTransferPlan(
            gameweeks=[], total_projected_points=0.0, total_hit_cost=0,
            total_net_projected_points=0.0, solver_status="OPTIMAL", methodology="test",
        )

    monkeypatch.setattr(api, "plan_horizon_transfers", fake_plan)

    response = TestClient(api.app).post(
        "/transfers/plan/horizon?pre_season=true&decision_hit_penalty=6&churn_penalty=2.5",
        json={"player_ids": [], "bank": 0, "free_transfers": 0},
        headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 200
    assert captured["pre_season"] is True
    assert captured["decision_hit_penalty"] == 6.0
    assert captured["churn_penalty"] == 2.5


def test_scheduler_ticks_returns_recent_ticks_newest_first(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    from aifpl.artifacts import json_bytes, write_immutable
    from aifpl.scheduler import SchedulerTickResult
    from datetime import datetime, timezone

    ticks_dir = tmp_path / "scheduler" / "ticks"
    ticks_dir.mkdir(parents=True)
    for stamp, status in (("20260815T100000000000Z-1", "succeeded"), ("20260815T090000000000Z-2", "not_due")):
        write_immutable(
            ticks_dir / f"{stamp}.json",
            json_bytes(SchedulerTickResult(
                status=status, checked_at=datetime(2026, 8, 15, 9, tzinfo=timezone.utc),
                event=1, deadline=datetime(2026, 8, 21, 18, tzinfo=timezone.utc),
                refresh_at=datetime(2026, 8, 21, 16, 30, tzinfo=timezone.utc),
                season_id="2026-27", missed=False, forced=False, output_path=f"ticks/{stamp}.json",
            ).model_dump(mode="json"), pretty=True),
        )

    response = TestClient(api.app).get("/scheduler/ticks?limit=10")

    assert response.status_code == 200
    assert response.json()[0]["status"] == "succeeded"


def test_calibration_backtests_lists_manifested_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    import json as json_module
    from pathlib import Path

    run_dir = tmp_path / "backtests" / "2025-26" / "RUN_A"
    run_dir.mkdir(parents=True)
    (run_dir / "predictions.jsonl").write_text("")
    (run_dir / "backtest.manifest.json").write_text(json_module.dumps({
        "created_at": "2026-08-15T10:00:00+00:00",
        "artifact_path": "backtests/2025-26/RUN_A/predictions.jsonl",
        "parameters": {"season": "2025-26", "window": 5},
        "metrics": {"predictions": 100, "mae": 2.5, "rmse": 3.1, "bias": 0.2, "coverage": 0.9},
    }))

    response = TestClient(api.app).get("/calibration/backtests")

    assert response.status_code == 200
    rows = response.json()
    assert rows[0]["season"] == "2025-26"
    assert rows[0]["run"] == "RUN_A"
    assert rows[0]["metrics"]["mae"] == 2.5
    assert rows[0]["comparable"] is False


def test_hermes_decision_history_is_empty_without_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)

    response = TestClient(api.app).get("/hermes/decisions")

    assert response.status_code == 200
    assert response.json() == []


def test_hermes_run_transcript_is_not_found_without_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)

    response = TestClient(api.app).get("/hermes/runs/latest")

    assert response.status_code == 404


def test_hermes_run_transcript_endpoint_returns_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    transcript = api.HermesRunTranscript(
        created_at=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
        gameweek=1, season_id="2026-27", outcome="succeeded", tool_steps=2,
        messages=[{"role": "assistant", "tool_calls": [{"function": {"name": "set_strategy"}}]}],
    )

    class FakeManager:
        def decisions(self, limit):
            return []
        def latest_transcript(self):
            return transcript

    monkeypatch.setattr(api, "HermesManager", lambda root: FakeManager())

    response = TestClient(api.app).get("/hermes/runs/latest")

    assert response.status_code == 200
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["tool_steps"] == 2


def test_hermes_supersede_endpoint_uses_the_current_gameweek(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    captured: dict[str, object] = {}

    class FakeScheduler:
        def __init__(self, root) -> None:
            assert root == tmp_path

        def status(self):
            return SimpleNamespace(event=2, season_id="2026-27", missed=False)

    class FakeManager:
        def __init__(self, root) -> None:
            assert root == tmp_path

        def supersede_decision(self, *args):
            captured["args"] = args
            return {
                "decision": {
                    "action": "hold", "gameweek": 2,
                    "squad": {
                        "player_ids": list(range(1, 16)), "bank": 0, "free_transfers": 2,
                        "purchase_prices": {str(player_id): 50 for player_id in range(1, 16)},
                    },
                    "captain_id": 1, "starting_xi_ids": list(range(1, 12)),
                    "transfers_out": [], "transfers_in": [], "explanation": "Corrected plan.",
                    "strategy": {
                        "risk_tolerance": 0.5, "hit_aversion": 0.7, "differential_appetite": 0.4,
                        "planning_horizon": 4, "preferred_players": [], "rationale": "Test strategy.",
                    },
                    "model": "deterministic_correction", "created_at": "2026-08-28T08:30:00Z",
                    "backend_methodology": "test", "decision_path": "/tmp/replacement.json",
                    "state_path": "/tmp/replacement-state.json", "season_id": "2026-27",
                },
                "state_path": "/tmp/replacement-state.json", "correction_path": "/tmp/correction.json",
            }

    monkeypatch.setattr(api, "DeadlineScheduler", FakeScheduler)
    monkeypatch.setattr(api, "HermesManager", FakeManager)

    response = TestClient(api.app).post(
        "/hermes/decisions/supersede",
        json={
            "base_state_id": "20260815T215213855640Z.json",
            "supersedes_decision_id": "20260828T080815145156Z.json",
            "reason": "Normal-week transfer limit was missing.",
        },
        headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 201
    assert captured["args"] == (
        "20260815T215213855640Z.json",
        "20260828T080815145156Z.json",
        "Normal-week transfer limit was missing.",
        2,
        "2026-27",
    )
