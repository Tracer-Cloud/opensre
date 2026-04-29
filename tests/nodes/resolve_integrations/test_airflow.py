"""Tests for Airflow integration resolution and env var loading."""

from __future__ import annotations

import pytest

from app.nodes.resolve_integrations.node import _classify_integrations, _load_env_integrations


def test_classify_airflow_from_store_includes_integration_id() -> None:
    integrations = [
        {
            "id": "store-airflow",
            "service": "airflow",
            "status": "active",
            "credentials": {
                "base_url": "https://airflow.example.com/api/v1",
                "auth_token": "store-token",
                "timeout_seconds": 20,
                "verify_ssl": True,
                "max_results": 25,
            },
        }
    ]

    resolved = _classify_integrations(integrations)

    assert resolved["airflow"]["base_url"] == "https://airflow.example.com/api/v1"
    assert resolved["airflow"]["auth_token"] == "store-token"
    assert resolved["airflow"]["integration_id"] == "store-airflow"


def test_load_env_airflow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIRFLOW_BASE_URL", "https://airflow.example.com/api/v1/")
    monkeypatch.setenv("AIRFLOW_AUTH_TOKEN", "env-token")
    monkeypatch.setenv("AIRFLOW_VERIFY_SSL", "false")
    monkeypatch.setenv("AIRFLOW_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("AIRFLOW_MAX_RESULTS", "10")

    integrations = _load_env_integrations()
    airflow = [integration for integration in integrations if integration["service"] == "airflow"]

    assert len(airflow) == 1
    creds = airflow[0]["credentials"]
    assert airflow[0]["id"] == "env-airflow"
    assert creds["base_url"] == "https://airflow.example.com/api/v1"
    assert creds["auth_token"] == "env-token"
    assert creds["verify_ssl"] is False
    assert creds["timeout_seconds"] == 30.0
    assert creds["max_results"] == 10


def test_load_env_airflow_absent_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIRFLOW_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AIRFLOW_USERNAME", raising=False)

    integrations = _load_env_integrations()
    airflow = [integration for integration in integrations if integration["service"] == "airflow"]

    assert airflow == []
