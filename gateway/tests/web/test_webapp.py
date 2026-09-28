"""Lightweight FastAPI smoke + telemetry coverage for ``gateway.web.webapp``."""

from __future__ import annotations

import importlib
from http import HTTPStatus
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.web import webapp


def test_webapp_module_calls_init_sentry_on_import(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: importing the module runs shared process boot, which is idempotent
    # per profile — an earlier import in this session already consumed it, so a
    # reload alone would assert against a no-op.
    from bootstrap.process import reset_process_runtime_for_tests

    init_mock = MagicMock()
    monkeypatch.setattr("infrastructure.observability.errors.sentry.init_sentry", init_mock)
    reset_process_runtime_for_tests()

    # Act
    importlib.reload(webapp)

    # Assert: the web entrypoint still reports crashes, now via WEB_PROFILE
    # rather than a direct call.
    init_mock.assert_called_once()


def test_health_response_returns_known_fields() -> None:
    response = webapp.get_health_response()

    assert hasattr(response, "ok")
    assert hasattr(response, "version")
    assert hasattr(response, "llm_configured")
    assert hasattr(response, "env")
    assert response.sessions >= 0 and response.scheduled_tasks >= 0
    assert isinstance(response.previous_shutdown, str)


def test_ok_route_is_registered() -> None:
    client = TestClient(webapp.app)
    resp = client.get("/ok")
    assert resp.status_code in (
        HTTPStatus.OK,
        HTTPStatus.SERVICE_UNAVAILABLE,
    )
    data = resp.json()
    assert "ok" in data
    assert "version" in data
