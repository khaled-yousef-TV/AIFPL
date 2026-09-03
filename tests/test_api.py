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


def test_rank_captaincy_route_requires_a_saved_rank_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    players = [
        {
            "player_id": 1,
            "player_name": "Player 1",
            "position": "MID",
            "club": "A",
            "cost": 100,
            "projected_points": 8.0,
            "availability_multiplier": 1.0,
        },
        {
            "player_id": 2,
            "player_name": "Player 2",
            "position": "MID",
            "club": "B",
            "cost": 100,
            "projected_points": 7.5,
            "availability_multiplier": 1.0,
        },
    ]

    response = TestClient(api.app).post(
        "/captaincy/plan?objective_mode=RANK_MODE",
        json={"players": players},
        headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 422
    assert "saved GameState" in response.json()["detail"]


def test_game_state_route_round_trips_rank_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    payload = {
        "season_id": "2026-27",
        "gameweek": 27,
        "overall_rank": 184000,
        "target_rank": 50000,
        "free_transfers": 3,
        "bank": 80,
        "objective_mode": "RANK_MODE",
    }

    response = TestClient(api.app).post(
        "/game-state", json=payload, headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )
    assert response.status_code == 201
    assert response.json()["strategy_status"] == "BEHIND_TARGET"

    latest = TestClient(api.app).get(
        "/game-state", headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )
    assert latest.status_code == 200
    assert latest.json()["rank_gap_ratio"] == 3.68


def test_account_game_state_route_imports_public_account_payloads(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)

    class FakeFplClient:
        async def fetch_entry_history(self, entry_id):
            assert entry_id == 123
            return {"current": [{"event": 2, "overall_rank": 184000, "bank": 83}]}

        async def fetch_entry_picks(self, entry_id, event):
            assert (entry_id, event) == (123, 2)
            return {
                "active_chip": None,
                "entry_history": {"event": 2},
                "picks": [
                    {
                        "element": player_id,
                        "position": player_id,
                        "is_captain": player_id == 1,
                        "is_vice_captain": player_id == 2,
                    }
                    for player_id in range(1, 16)
                ],
            }

    monkeypatch.setattr(api, "FplClient", FakeFplClient)
    response = TestClient(api.app).post(
        "/game-state/account",
        json={
            "entry_id": 123,
            "season_id": "2026-27",
            "target_rank": 50000,
            "free_transfers": 2,
            "chips_remaining": {"wildcard": 1},
        },
        headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 201
    assert response.json()["account_id"] == 123
    assert (tmp_path / "account" / "2026-27" / "123").is_dir()


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


def test_hermes_decision_history_passes_scope_filters(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    captured: dict[str, object] = {}

    class FakeManager:
        def decisions(self, limit, season_id=None, gameweek=None):
            captured.update(limit=limit, season_id=season_id, gameweek=gameweek)
            return []

    monkeypatch.setattr(api, "HermesManager", lambda root: FakeManager())

    response = TestClient(api.app).get(
        "/hermes/decisions?limit=7&season_id=2026-27&gameweek=3",
    )

    assert response.status_code == 200
    assert captured == {"limit": 7, "season_id": "2026-27", "gameweek": 3}


def test_execution_confirmation_route_requires_admin_and_persists_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    captured: dict[str, object] = {}
    record = api.ExecutionConfirmation(
        decision_path=str(tmp_path / "decision.json"), season_id="2026-27", gameweek=1,
        source="manual", squad_ids=list(range(1, 16)), starting_xi_ids=list(range(1, 12)),
        bench_ids=[12, 13, 14, 15], captain_id=1, vice_captain_id=2,
        confirmed_at=datetime(2026, 8, 14, 17, tzinfo=timezone.utc),
        output_path=str(tmp_path / "confirmation.json"),
    )

    class FakeStore:
        def __init__(self, root) -> None:
            assert root == tmp_path

        def confirm(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return record

    monkeypatch.setattr(api, "ExecutionConfirmationStore", FakeStore)
    payload = {
        "decision_path": "hermes/decisions/decision.json",
        "squad_ids": list(range(1, 16)),
        "starting_xi_ids": list(range(1, 12)),
        "bench_ids": [12, 13, 14, 15],
        "captain_id": 1,
        "vice_captain_id": 2,
    }

    denied = TestClient(api.app).post("/execution/confirmations", json=payload)
    allowed = TestClient(api.app).post(
        "/execution/confirmations", json=payload,
        headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 201
    assert captured["args"] == ("hermes/decisions/decision.json",)
    assert captured["kwargs"]["source"] == "manual"


def test_execution_confirmation_read_requires_admin(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")

    response = TestClient(api.app).get(
        "/execution/confirmations/latest?season_id=2026-27&gameweek=1",
    )

    assert response.status_code == 401


def test_hermes_run_transcript_is_not_found_without_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")

    response = TestClient(api.app).get(
        "/hermes/runs/latest", headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 404


def test_hermes_run_transcript_endpoint_returns_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
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

    response = TestClient(api.app).get(
        "/hermes/runs/latest", headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["tool_steps"] == 2


def test_sensitive_debug_reads_require_admin_access(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.delenv("AIFPL_ALLOW_ANONYMOUS_SENSITIVE_READS", raising=False)

    denied = TestClient(api.app).get("/debug/env")
    allowed = TestClient(api.app).get(
        "/debug/env", headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200


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


def test_hermes_replan_current_endpoint_passes_chip_selection(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(api, "data_dir", lambda: tmp_path)
    captured: dict[str, object] = {}
    decision = {
        "action": "hold", "gameweek": 3,
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
    }

    class FakeManager:
        def __init__(self, root) -> None:
            assert root == tmp_path

        def replan_current(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"decision": decision, "state_path": "/tmp/replacement-state.json", "correction_path": "/tmp/correction.json"}

    monkeypatch.setattr(api, "HermesManager", FakeManager)

    response = TestClient(api.app).post(
        "/hermes/replan-current",
        json={
            "reason": "Account sync completed after the stale recommendation.",
            "active_chip": "wildcard",
            "active_chip_set": 1,
        },
        headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 201
    assert captured["args"] == ("Account sync completed after the stale recommendation.",)
    assert captured["kwargs"] == {"active_chip": "wildcard", "active_chip_set": 1}
