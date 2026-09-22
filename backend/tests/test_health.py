from pathlib import Path

from fastapi.testclient import TestClient

from app import main


def test_legacy_health_is_kept(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_does_not_touch_database_or_storage(
    client: TestClient, monkeypatch
):
    def unexpected_check() -> bool:
        raise AssertionError("liveness must not check dependencies")

    monkeypatch.setattr(main, "database_is_ready", unexpected_check)
    monkeypatch.setattr(main, "document_storage_is_ready", unexpected_check)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_readiness_checks_database_and_document_storage(client: TestClient):
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_rejects_database_failure_without_details(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(main, "database_is_ready", lambda: False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_readiness_rejects_missing_document_storage(
    client: TestClient, monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(main.settings, "document_storage_root", tmp_path / "missing")

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
