from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Environment
from app.webapp import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)

class TestWebApp:
    def test_health_ok(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("app.webapp._graph_loaded", lambda: True)
        monkeypatch.setattr("app.webapp._llm_configured", lambda: True)
        monkeypatch.setattr("app.webapp.get_version", lambda: "0.1.0")
        monkeypatch.setattr("app.webapp.get_environment", lambda: Environment.PRODUCTION)
        expected_response = {
            "ok": True,
            "version": "0.1.0",
            "graph_loaded": True,
            "llm_configured": True,
            "env": "production",
        }
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == expected_response

    def test_health_failed(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("app.webapp._graph_loaded", lambda: False)
        monkeypatch.setattr("app.webapp._llm_configured", lambda: True)
        monkeypatch.setattr("app.webapp.get_version", lambda: "0.1.0")
        monkeypatch.setattr("app.webapp.get_environment", lambda: Environment.PRODUCTION)
        expected_response = {
            "ok": False,
            "version": "0.1.0",
            "graph_loaded": False,
            "llm_configured": True,
            "env": "production",
        }
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json() == expected_response
