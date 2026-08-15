from datetime import datetime, timezone

from fastapi.testclient import TestClient

from aifpl import api
from aifpl.dashboard import CurrentDashboard


def test_health_endpoint() -> None:
    response = TestClient(api.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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

    class FakeStore:
        def latest(self, catalog_id=None):
            return []

    monkeypatch.setattr(api, "OddsProjectionStore", lambda root: FakeStore())

    def fake_plan(rows, state, **kwargs):
        captured.update(kwargs)
        return api.HorizonTransferPlan(
            gameweeks=[], total_projected_points=0.0, total_hit_cost=0,
            total_net_projected_points=0.0, solver_status="OPTIMAL", methodology="test",
        )

    monkeypatch.setattr(api, "plan_horizon_transfers", fake_plan)

    response = TestClient(api.app).post(
        "/transfers/plan/horizon?pre_season=true&decision_hit_penalty=6",
        json={"player_ids": [], "bank": 0, "free_transfers": 0},
        headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 200
    assert captured["pre_season"] is True
    assert captured["decision_hit_penalty"] == 6.0


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
