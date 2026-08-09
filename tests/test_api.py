from fastapi.testclient import TestClient

from aifpl import api


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
