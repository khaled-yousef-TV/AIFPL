from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aifpl import api
from aifpl.cli import app


def test_api_exposes_every_pipeline_stage() -> None:
    routes = {(route.path, method) for route in api.app.routes for method in getattr(route, "methods", set())}
    expected = {
        ("/snapshots/fpl/bootstrap", "POST"), ("/snapshots/fpl/fixtures", "POST"),
        ("/snapshots/fpl/events/{event}", "POST"), ("/historical/seasons/{season}/imports", "POST"),
        ("/historical/seasons/{season}/backtests/baseline", "POST"),
        ("/catalogs/current/players", "POST"), ("/catalogs/current/fixtures", "POST"),
        ("/projection-catalogs/current", "POST"), ("/projection-catalogs/xg-xa", "POST"),
        ("/projection-catalogs/fixtures", "POST"), ("/projection-catalogs/odds", "POST"),
        ("/odds/epl/snapshots", "POST"), ("/odds/epl/fixture-consensus", "POST"),
        ("/transfers/hit-cost", "POST"),
        ("/health/sources/check", "POST"), ("/health/sources", "GET"),
        ("/config/team-aliases", "GET"),
        ("/jobs/refresh/current", "POST"), ("/jobs/refresh/current/latest", "GET"),
        ("/scheduler/status", "GET"), ("/scheduler/tick", "POST"),
        ("/transfers/plan/horizon", "POST"),
        ("/calibration/backtests", "POST"),
        ("/calibration/compare", "POST"),
        ("/evidence/players/build", "POST"), ("/evidence/players", "GET"),
        ("/odds/epl/event-markets", "POST"), ("/odds/epl/market-signals", "POST"),
        ("/hermes/run", "POST"), ("/hermes/state", "GET"), ("/hermes/decisions/latest", "GET"),
        ("/hermes/migrate", "POST"), ("/hermes/decisions/supersede", "POST"),
    }
    assert expected <= routes


def test_transfer_hit_cost_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")
    response = TestClient(api.app).post(
        "/transfers/hit-cost", json={"transfers_made": 3, "free_transfers": 1},
        headers={"X-AIFPL-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 200
    assert response.json() == {"hit_cost": 8}


def test_mutating_api_requires_admin_key(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_ADMIN_API_KEY", "test-admin-key")

    response = TestClient(api.app).post("/transfers/hit-cost", json={"transfers_made": 0, "free_transfers": 0})

    assert response.status_code == 401


def test_cli_exposes_documented_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "check-source-health", "latest-source-health", "team-aliases", "fetch-bootstrap", "import-season",
        "refresh-current-data", "latest-refresh-job", "backtest-baseline", "build-odds-projections",
        "scheduler-status", "run-scheduler-tick", "run-deadline-scheduler",
        "fixture-projections", "odds-projections", "plan-transfers", "plan-horizon",
        "calibrate-backtest",
        "compare-backtests",
        "build-player-evidence", "player-evidence",
        "fetch-event-markets", "build-market-signals",
        "hermes-run", "hermes-reinitialize-opening-squad", "hermes-state", "hermes-decision",
        "hermes-migrate-state",
    ):
        assert command in result.stdout
