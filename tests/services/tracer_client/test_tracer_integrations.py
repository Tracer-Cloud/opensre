"""Tests for Tracer integration credential helpers."""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from typing import Any

from app.services.tracer_client.tracer_integrations import (
    GrafanaIntegrationCredentials,
    TracerIntegrationsMixin,
)


class DummyTracerClient(TracerIntegrationsMixin):
    """Dummy client to isolate and test the TracerIntegrationsMixin."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(
            base_url="https://tracer.example.com",
            org_id="test_org_123",
            jwt_token="invalid-test-token",
        )
        self._payload = payload
        self.last_endpoint = ""
        self.last_params: dict[str, Any] = {}

    def _get(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.last_endpoint = endpoint
        self.last_params = dict(params or {})
        return copy.deepcopy(self._payload)


def test_get_integration_credentials_uses_service_filter() -> None:
    """Fetch credentials for one service using org and service filters."""
    payload = {
        "success": True,
        "data": [{"id": "grafana-1", "credentials": {"endpoint": "https://grafana.example.com"}}],
    }
    client = DummyTracerClient(payload)

    result = client.get_integration_credentials("Grafana")

    assert result == payload["data"]
    assert client.last_endpoint == "/api/integrations"
    assert client.last_params == {"orgId": "test_org_123", "service": "Grafana"}


def test_get_integration_credentials_returns_empty_when_request_fails() -> None:
    """Return an empty list when the integrations API has no usable data."""
    client = DummyTracerClient({"success": False, "data": []})

    assert client.get_integration_credentials("Grafana") == []


def test_get_all_integrations_parses_string_credentials_and_preserves_dicts() -> None:
    """Parse JSON string credentials while keeping dict credentials unchanged."""
    payload = {
        "success": True,
        "data": [
            {
                "id": "grafana-1",
                "service": "Grafana",
                "credentials": '{"endpoint":"https://grafana.example.com","api_key":"token-123"}',
            },
            {
                "id": "slack-1",
                "service": "Slack",
                "credentials": {"channel": "#alerts"},
            },
        ],
    }
    client = DummyTracerClient(payload)

    result = client.get_all_integrations()

    assert result[0]["credentials"] == {
        "endpoint": "https://grafana.example.com",
        "api_key": "token-123",
    }
    assert result[1]["credentials"] == {"channel": "#alerts"}
    assert client.last_endpoint == "/api/integrations"
    assert client.last_params == {"orgId": "test_org_123"}


def test_get_all_integrations_falls_back_to_empty_credentials_on_malformed_json(
    caplog,
) -> None:
    """Replace malformed credential JSON with an empty dict and warn."""
    payload = {
        "success": True,
        "data": [
            {
                "id": "grafana-1",
                "service": "Grafana",
                "credentials": '{"endpoint": "https://grafana.example.com"',
            }
        ],
    }
    client = DummyTracerClient(payload)

    with caplog.at_level(
        logging.WARNING,
        logger="app.services.tracer_client.tracer_integrations",
    ):
        result = client.get_all_integrations()

    assert result[0]["credentials"] == {}
    assert "Malformed credentials JSON for integration grafana-1" in caplog.text


def test_get_grafana_credentials_prefers_active_integration() -> None:
    """Prefer the active Grafana integration over an earlier inactive record."""
    payload = {
        "success": True,
        "data": [
            {
                "id": "grafana-inactive",
                "status": "inactive",
                "credentials": {
                    "endpoint": "https://inactive.grafana.example.com",
                    "api_key": "inactive-token",
                },
            },
            {
                "id": "grafana-active",
                "status": "active",
                "credentials": (
                    '{"endpoint":"https://active.grafana.example.com","api_key":"active-token"}'
                ),
            },
        ],
    }
    client = DummyTracerClient(payload)

    result = client.get_grafana_credentials()

    assert result == GrafanaIntegrationCredentials(
        found=True,
        endpoint="https://active.grafana.example.com",
        api_key="active-token",
        integration_id="grafana-active",
        status="active",
    )


def test_get_grafana_credentials_returns_not_found_when_no_integrations() -> None:
    """Return found=False when no Grafana integrations exist."""
    client = DummyTracerClient({"success": True, "data": []})

    result = client.get_grafana_credentials()

    assert result == GrafanaIntegrationCredentials(found=False)


def test_get_grafana_credentials_falls_back_to_first_inactive_integration() -> None:
    """Use the first integration when no Grafana records are active."""
    payload = {
        "success": True,
        "data": [
            {
                "id": "grafana-inactive-1",
                "status": "inactive",
                "credentials": {
                    "endpoint": "https://inactive-one.grafana.example.com",
                    "api_key": "inactive-one-token",
                },
            },
            {
                "id": "grafana-inactive-2",
                "status": "pending",
                "credentials": {
                    "endpoint": "https://inactive-two.grafana.example.com",
                    "api_key": "inactive-two-token",
                },
            },
        ],
    }
    client = DummyTracerClient(payload)

    result = client.get_grafana_credentials()

    assert result == GrafanaIntegrationCredentials(
        found=True,
        endpoint="https://inactive-one.grafana.example.com",
        api_key="inactive-one-token",
        integration_id="grafana-inactive-1",
        status="inactive",
    )


def test_get_grafana_credentials_handles_malformed_json() -> None:
    """Return found credentials with empty fields when Grafana JSON is malformed."""
    payload = {
        "success": True,
        "data": [
            {
                "id": "grafana-1",
                "status": "active",
                "credentials": '{"endpoint":"https://grafana.example.com"',
            }
        ],
    }
    client = DummyTracerClient(payload)

    result = client.get_grafana_credentials()

    assert result == GrafanaIntegrationCredentials(
        found=True,
        endpoint="",
        api_key="",
        integration_id="grafana-1",
        status="active",
    )
    assert result.is_configured is False
