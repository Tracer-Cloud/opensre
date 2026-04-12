from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Environment
from app.webapp import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_ok_returns_200_and_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.webapp._graph_loaded", lambda: True)
    monkeypatch.setattr("app.webapp._llm_configured", lambda: True)
    monkeypatch.setattr("app.webapp.get_version", lambda: "0.1.0")
    monkeypatch.setattr("app.webapp.get_environment", lambda: Environment.PRODUCTION)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "version": "0.1.0",
        "graph_loaded": True,
        "llm_configured": True,
        "env": "production",
    }


def test_health_unhealthy_returns_503_and_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.webapp._graph_loaded", lambda: False)
    monkeypatch.setattr("app.webapp._llm_configured", lambda: True)
    monkeypatch.setattr("app.webapp.get_version", lambda: "0.1.0")
    monkeypatch.setattr("app.webapp.get_environment", lambda: Environment.DEVELOPMENT)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "version": "0.1.0",
        "graph_loaded": False,
        "llm_configured": True,
        "env": "development",
    }


def test_health_payload_has_stable_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.webapp._graph_loaded", lambda: True)
    monkeypatch.setattr("app.webapp._llm_configured", lambda: False)
    monkeypatch.setattr("app.webapp.get_version", lambda: "0.1.0")
    monkeypatch.setattr("app.webapp.get_environment", lambda: Environment.PRODUCTION)

    response = client.get("/health")

    assert sorted(response.json().keys()) == [
        "env",
        "graph_loaded",
        "llm_configured",
        "ok",
        "version",
    ]
