"""Alertmanager E2E tests verifying integration with investigation pipeline.

Tests:
- Alertmanager config resolution from store and env
- Alertmanager verification (connectivity, status check)
- Alertmanager source detection in investigation context
- Alertmanager tools availability and structure
- Alert fixture validity (Alertmanager webhook format)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.nodes.plan_actions.detect_sources import detect_sources
from app.nodes.resolve_integrations.node import _classify_integrations


class TestAlertmanagerIntegrationResolution:
    """Test Alertmanager config resolution from multiple sources."""

    def test_alertmanager_resolution_from_store(self):
        """Alertmanager integration correctly resolved from local store."""
        integrations = [
            {
                "id": "alertmanager-prod",
                "service": "alertmanager",
                "status": "active",
                "credentials": {
                    "base_url": "http://alertmanager.monitoring.svc:9093",
                    "bearer_token": "",
                    "username": "",
                    "password": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "alertmanager" in resolved
        assert resolved["alertmanager"]["base_url"] == "http://alertmanager.monitoring.svc:9093"

    def test_alertmanager_resolution_with_bearer_token(self):
        """Alertmanager integration with bearer token is correctly resolved."""
        integrations = [
            {
                "id": "alertmanager-auth",
                "service": "alertmanager",
                "status": "active",
                "credentials": {
                    "base_url": "https://alertmanager.example.com",
                    "bearer_token": "my-secret-token",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "alertmanager" in resolved
        assert resolved["alertmanager"]["base_url"] == "https://alertmanager.example.com"
        assert resolved["alertmanager"]["bearer_token"] == "my-secret-token"

    def test_alertmanager_resolution_with_basic_auth(self):
        """Alertmanager integration with basic auth is correctly resolved."""
        integrations = [
            {
                "id": "alertmanager-basic",
                "service": "alertmanager",
                "status": "active",
                "credentials": {
                    "base_url": "https://alertmanager.example.com",
                    "username": "admin",
                    "password": "secret",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "alertmanager" in resolved
        assert resolved["alertmanager"]["username"] == "admin"
        assert resolved["alertmanager"]["password"] == "secret"

    def test_alertmanager_empty_base_url_skipped(self):
        """Alertmanager integration without base_url is safely skipped."""
        integrations = [
            {
                "id": "bad-alertmanager",
                "service": "alertmanager",
                "status": "active",
                "credentials": {
                    "base_url": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "alertmanager" not in resolved

    def test_alertmanager_inactive_integration_skipped(self):
        """Inactive Alertmanager integration is not resolved."""
        integrations = [
            {
                "id": "alertmanager-inactive",
                "service": "alertmanager",
                "status": "inactive",
                "credentials": {
                    "base_url": "http://alertmanager.example.com:9093",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "alertmanager" not in resolved

    def test_alertmanager_url_trailing_slash_normalized(self):
        """Trailing slash is stripped from Alertmanager base_url."""
        integrations = [
            {
                "id": "alertmanager-slash",
                "service": "alertmanager",
                "status": "active",
                "credentials": {
                    "base_url": "http://alertmanager.example.com:9093/",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "alertmanager" in resolved
        assert resolved["alertmanager"]["base_url"] == "http://alertmanager.example.com:9093"


class TestAlertmanagerEnvResolution:
    """Test Alertmanager config resolution from environment variables."""

    def test_alertmanager_resolved_from_env(self, monkeypatch):
        """Alertmanager integration resolved from ALERTMANAGER_URL env var."""
        monkeypatch.setenv("ALERTMANAGER_URL", "http://alertmanager.monitoring.svc:9093")

        from app.nodes.resolve_integrations.node import _load_env_integrations

        env_integrations = _load_env_integrations()
        alertmanager_records = [i for i in env_integrations if i["service"] == "alertmanager"]

        assert len(alertmanager_records) == 1
        creds = alertmanager_records[0]["credentials"]
        assert creds["base_url"] == "http://alertmanager.monitoring.svc:9093"

    def test_alertmanager_bearer_token_from_env(self, monkeypatch):
        """Alertmanager bearer token loaded from env var."""
        monkeypatch.setenv("ALERTMANAGER_URL", "https://alertmanager.example.com")
        monkeypatch.setenv("ALERTMANAGER_BEARER_TOKEN", "env-token-123")

        from app.nodes.resolve_integrations.node import _load_env_integrations

        env_integrations = _load_env_integrations()
        alertmanager_records = [i for i in env_integrations if i["service"] == "alertmanager"]

        assert len(alertmanager_records) == 1
        assert alertmanager_records[0]["credentials"]["bearer_token"] == "env-token-123"

    def test_alertmanager_not_loaded_when_url_missing(self, monkeypatch):
        """Alertmanager integration is not loaded from env when ALERTMANAGER_URL is unset."""
        monkeypatch.delenv("ALERTMANAGER_URL", raising=False)

        from app.nodes.resolve_integrations.node import _load_env_integrations

        env_integrations = _load_env_integrations()
        alertmanager_records = [i for i in env_integrations if i["service"] == "alertmanager"]

        assert len(alertmanager_records) == 0


class TestAlertmanagerSourceDetection:
    """Test Alertmanager source detection in investigation context."""

    def test_alertmanager_detected_when_configured(self):
        """Alertmanager source is detected when integration is configured."""
        raw_alert = {
            "commonLabels": {"alertname": "HighErrorRate", "job": "api-server"},
            "commonAnnotations": {"summary": "High error rate"},
        }
        resolved_integrations = {
            "alertmanager": {
                "base_url": "http://alertmanager.monitoring.svc:9093",
                "bearer_token": "",
                "username": "",
                "password": "",
            }
        }

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "alertmanager" in sources
        assert sources["alertmanager"]["base_url"] == "http://alertmanager.monitoring.svc:9093"
        assert sources["alertmanager"]["connection_verified"] is True

    def test_alertmanager_filter_labels_populated_from_alertname(self):
        """Alertmanager source carries alertname label filter extracted from alert."""
        raw_alert = {
            "commonLabels": {"alertname": "HighErrorRate"},
            "commonAnnotations": {},
        }
        resolved_integrations = {
            "alertmanager": {
                "base_url": "http://alertmanager.monitoring.svc:9093",
            }
        }

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "alertmanager" in sources
        assert 'alertname="HighErrorRate"' in sources["alertmanager"]["filter_labels"]

    def test_alertmanager_not_detected_when_unconfigured(self):
        """Alertmanager source is not included when integration is not configured."""
        raw_alert = {"commonLabels": {"alertname": "HighErrorRate"}}
        resolved_integrations = {}

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "alertmanager" not in sources

    def test_alertmanager_not_detected_when_base_url_empty(self):
        """Alertmanager source is not included when base_url is empty."""
        raw_alert = {"commonLabels": {}}
        resolved_integrations = {
            "alertmanager": {
                "base_url": "",
            }
        }

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "alertmanager" not in sources


class TestAlertmanagerVerification:
    """Test Alertmanager integration verification flow."""

    @patch("app.services.alertmanager.client.AlertmanagerClient.get_status")
    def test_verify_alertmanager_success(self, mock_get_status, monkeypatch):
        """Alertmanager verification passes when status endpoint responds successfully."""
        monkeypatch.setenv("ALERTMANAGER_URL", "http://alertmanager.monitoring.svc:9093")
        mock_get_status.return_value = {
            "success": True,
            "status": {
                "cluster": {"status": "ready"},
                "versionInfo": {"version": "0.27.0"},
            },
        }

        from app.integrations.verify import verify_integrations

        results = verify_integrations(service="alertmanager")

        assert len(results) >= 1
        am_result = next((r for r in results if r["service"] == "alertmanager"), None)
        assert am_result is not None
        assert am_result["status"] == "passed"
        assert "alertmanager" in am_result["detail"].lower()

    @patch("app.services.alertmanager.client.AlertmanagerClient.get_status")
    def test_verify_alertmanager_failure(self, mock_get_status, monkeypatch):
        """Alertmanager verification fails when status endpoint is unreachable."""
        monkeypatch.setenv("ALERTMANAGER_URL", "http://alertmanager.monitoring.svc:9093")
        mock_get_status.return_value = {
            "success": False,
            "error": "Connection refused",
        }

        from app.integrations.verify import verify_integrations

        results = verify_integrations(service="alertmanager")

        am_result = next((r for r in results if r["service"] == "alertmanager"), None)
        assert am_result is not None
        assert am_result["status"] == "failed"

    def test_verify_alertmanager_missing_when_not_configured(self, monkeypatch):
        """Alertmanager verification returns 'missing' when not configured."""
        monkeypatch.delenv("ALERTMANAGER_URL", raising=False)

        from app.integrations.verify import verify_integrations

        results = verify_integrations(service="alertmanager")

        am_result = next((r for r in results if r["service"] == "alertmanager"), None)
        assert am_result is not None
        assert am_result["status"] == "missing"

    def test_verify_integrations_result_structure(self, monkeypatch):
        """Verify integrations result has expected fields for alertmanager."""
        monkeypatch.delenv("ALERTMANAGER_URL", raising=False)

        from app.integrations.verify import verify_integrations

        results = verify_integrations(service="alertmanager")
        assert isinstance(results, list)
        for result in results:
            if result["service"] == "alertmanager":
                assert "service" in result
                assert "source" in result
                assert "status" in result
                assert "detail" in result
                assert result["status"] in ("passed", "missing", "failed")


class TestAlertmanagerToolsAvailability:
    """Test Alertmanager tools are importable and structurally correct."""

    def test_alertmanager_alerts_tool_importable(self):
        """AlertmanagerAlertsTool can be imported and is correctly typed."""
        from app.tools.AlertmanagerAlertsTool import AlertmanagerAlertsTool, alertmanager_alerts

        assert alertmanager_alerts is not None
        assert isinstance(alertmanager_alerts, AlertmanagerAlertsTool)
        assert alertmanager_alerts.name == "alertmanager_alerts"
        assert alertmanager_alerts.source == "alertmanager"

    def test_alertmanager_silences_tool_importable(self):
        """AlertmanagerSilencesTool can be imported and is correctly typed."""
        from app.tools.AlertmanagerSilencesTool import (
            AlertmanagerSilencesTool,
            alertmanager_silences,
        )

        assert alertmanager_silences is not None
        assert isinstance(alertmanager_silences, AlertmanagerSilencesTool)
        assert alertmanager_silences.name == "alertmanager_silences"
        assert alertmanager_silences.source == "alertmanager"

    def test_alertmanager_alerts_tool_not_available_without_source(self):
        """AlertmanagerAlertsTool reports unavailable when source has no connection_verified."""
        from app.tools.AlertmanagerAlertsTool import alertmanager_alerts

        assert not alertmanager_alerts.is_available({})
        assert not alertmanager_alerts.is_available({"alertmanager": {}})
        assert not alertmanager_alerts.is_available(
            {"alertmanager": {"connection_verified": False}}
        )

    def test_alertmanager_alerts_tool_available_with_verified_source(self):
        """AlertmanagerAlertsTool is available when alertmanager source is connection_verified."""
        from app.tools.AlertmanagerAlertsTool import alertmanager_alerts

        sources = {"alertmanager": {"connection_verified": True, "base_url": "http://am:9093"}}
        assert alertmanager_alerts.is_available(sources)

    def test_alertmanager_alerts_tool_extract_params(self):
        """AlertmanagerAlertsTool.extract_params returns correct fields from source."""
        from app.tools.AlertmanagerAlertsTool import alertmanager_alerts

        sources = {
            "alertmanager": {
                "base_url": "http://alertmanager.monitoring.svc:9093",
                "bearer_token": "tok",
                "username": "",
                "password": "",
                "filter_labels": ['alertname="HighErrorRate"'],
                "connection_verified": True,
            }
        }
        params = alertmanager_alerts.extract_params(sources)

        assert params["base_url"] == "http://alertmanager.monitoring.svc:9093"
        assert params["bearer_token"] == "tok"
        assert params["active"] is True

    @patch("app.services.alertmanager.client.AlertmanagerClient.list_alerts")
    def test_alertmanager_alerts_tool_run_success(self, mock_list_alerts):
        """AlertmanagerAlertsTool.run returns structured result on success."""
        from app.tools.AlertmanagerAlertsTool import alertmanager_alerts

        mock_list_alerts.return_value = {
            "success": True,
            "alerts": [
                {
                    "fingerprint": "abc123",
                    "status": "active",
                    "labels": {"alertname": "HighErrorRate", "job": "api"},
                    "annotations": {"summary": "High error rate"},
                    "starts_at": "2024-01-15T10:25:00Z",
                    "ends_at": "0001-01-01T00:00:00Z",
                    "generator_url": "http://prometheus:9090/graph",
                }
            ],
            "total": 1,
        }

        result = alertmanager_alerts.run(
            base_url="http://alertmanager.monitoring.svc:9093",
        )

        assert result["available"] is True
        assert result["source"] == "alertmanager"
        assert result["total"] == 1
        assert len(result["alerts"]) == 1
        assert len(result["firing_alerts"]) == 1

    def test_alertmanager_alerts_tool_run_missing_base_url(self):
        """AlertmanagerAlertsTool.run returns unavailable when base_url is empty."""
        from app.tools.AlertmanagerAlertsTool import alertmanager_alerts

        result = alertmanager_alerts.run(base_url="")

        assert result["available"] is False
        assert "not configured" in result["error"]

    @patch("app.services.alertmanager.client.AlertmanagerClient.list_silences")
    def test_alertmanager_silences_tool_run_success(self, mock_list_silences):
        """AlertmanagerSilencesTool.run returns structured result on success."""
        from app.tools.AlertmanagerSilencesTool import alertmanager_silences

        mock_list_silences.return_value = {
            "success": True,
            "silences": [
                {
                    "id": "silence-123",
                    "status": "active",
                    "matchers": [{"name": "alertname", "value": "HighErrorRate", "isEqual": True}],
                    "comment": "Planned maintenance",
                    "created_by": "oncall-engineer",
                    "starts_at": "2024-01-15T08:00:00Z",
                    "ends_at": "2024-01-15T12:00:00Z",
                }
            ],
            "active_silences": [
                {
                    "id": "silence-123",
                    "status": "active",
                    "matchers": [{"name": "alertname", "value": "HighErrorRate", "isEqual": True}],
                    "comment": "Planned maintenance",
                    "created_by": "oncall-engineer",
                    "starts_at": "2024-01-15T08:00:00Z",
                    "ends_at": "2024-01-15T12:00:00Z",
                }
            ],
            "total": 1,
        }

        result = alertmanager_silences.run(
            base_url="http://alertmanager.monitoring.svc:9093",
        )

        assert result["available"] is True
        assert result["source"] == "alertmanager_silences"
        assert result["total"] == 1
        assert len(result["active_silences"]) == 1


class TestAlertmanagerAlertFixture:
    """Test the Alertmanager alert fixture is valid and parseable."""

    def test_alertmanager_alert_fixture_is_valid_json(self):
        """Alertmanager alert fixture is valid JSON."""
        fixture_path = Path(__file__).parent / "alertmanager_alert.json"
        assert fixture_path.exists(), f"Alert fixture not found at {fixture_path}"

        with fixture_path.open() as f:
            alert = json.load(f)

        assert isinstance(alert, dict)

    def test_alertmanager_alert_fixture_has_required_webhook_fields(self):
        """Alert fixture contains Alertmanager webhook format fields."""
        fixture_path = Path(__file__).parent / "alertmanager_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        assert "version" in alert
        assert "status" in alert
        assert "commonLabels" in alert
        assert "commonAnnotations" in alert
        assert "alerts" in alert
        assert isinstance(alert["alerts"], list)
        assert len(alert["alerts"]) > 0

    def test_alertmanager_alert_fixture_individual_alerts_have_fingerprint(self):
        """Each alert in the fixture has Prometheus-specific fingerprint and generatorURL."""
        fixture_path = Path(__file__).parent / "alertmanager_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        for individual_alert in alert["alerts"]:
            assert "fingerprint" in individual_alert
            assert "generatorURL" in individual_alert
            assert "startsAt" in individual_alert

    def test_alertmanager_alert_fixture_has_service_labels(self):
        """Alert fixture contains service-level labels useful for investigation context."""
        fixture_path = Path(__file__).parent / "alertmanager_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        common_labels = alert.get("commonLabels", {})
        assert "alertname" in common_labels
        assert "severity" in common_labels
        assert "job" in common_labels


class TestAlertmanagerIntegrationConfig:
    """Test AlertmanagerIntegrationConfig model validation."""

    def test_alertmanager_config_creation(self):
        """AlertmanagerIntegrationConfig validates correctly with all fields."""
        from app.integrations.models import AlertmanagerIntegrationConfig

        config = AlertmanagerIntegrationConfig(
            base_url="http://alertmanager.monitoring.svc:9093",
            bearer_token="test-token",
            integration_id="test-id",
        )

        assert config.base_url == "http://alertmanager.monitoring.svc:9093"
        assert config.bearer_token == "test-token"
        assert config.integration_id == "test-id"
        assert config.username == ""
        assert config.password == ""

    def test_alertmanager_config_url_normalization(self):
        """AlertmanagerIntegrationConfig strips whitespace and trailing slash from base_url."""
        from app.integrations.models import AlertmanagerIntegrationConfig

        config = AlertmanagerIntegrationConfig(
            base_url="  http://alertmanager.example.com:9093/  ",
        )

        assert config.base_url == "http://alertmanager.example.com:9093"

    def test_alertmanager_config_basic_auth(self):
        """AlertmanagerIntegrationConfig stores basic auth credentials."""
        from app.integrations.models import AlertmanagerIntegrationConfig

        config = AlertmanagerIntegrationConfig(
            base_url="http://alertmanager.example.com:9093",
            username="admin",
            password="secret",
        )

        assert config.username == "admin"
        assert config.password == "secret"
        assert config.bearer_token == ""
