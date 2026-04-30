"""Unit tests for the VictoriaLogs integration.

Covers:
- ``VictoriaLogsIntegrationConfig`` model (base_url + tenant_id normalization)
- ``load_env_integrations`` env-var parsing
- ``_classify_service_instance`` DB-store classification
- ``_verify_victoria_logs`` (missing / failed / passed paths)

Important: the verifier returns ``status="passed"`` on success, not
``"verified"``. ``verification_exit_code()`` checks for ``"passed"``, so any
other string would silently fail the core gate even when the integration is
healthy.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.integrations.catalog import (
    _classify_service_instance,
    load_env_integrations,
)
from app.integrations.models import VictoriaLogsIntegrationConfig
from app.integrations.verify import _verify_victoria_logs


class TestVictoriaLogsIntegrationConfig:
    """Tests for the Pydantic config model."""

    def test_base_url_strip_and_rstrip_slash(self) -> None:
        config = VictoriaLogsIntegrationConfig(base_url="  http://vmlogs:9428/  ")
        assert config.base_url == "http://vmlogs:9428"

    def test_tenant_id_unset_normalizes_to_none(self) -> None:
        config = VictoriaLogsIntegrationConfig(base_url="http://vmlogs:9428")
        assert config.tenant_id is None

    def test_tenant_id_empty_string_normalizes_to_none(self) -> None:
        config = VictoriaLogsIntegrationConfig(base_url="http://vmlogs:9428", tenant_id="")
        assert config.tenant_id is None

    def test_tenant_id_whitespace_normalizes_to_none(self) -> None:
        config = VictoriaLogsIntegrationConfig(base_url="http://vmlogs:9428", tenant_id="   ")
        assert config.tenant_id is None

    def test_tenant_id_explicit_value_preserved(self) -> None:
        config = VictoriaLogsIntegrationConfig(base_url="http://vmlogs:9428", tenant_id="acme")
        assert config.tenant_id == "acme"

    def test_integration_id_default_empty(self) -> None:
        config = VictoriaLogsIntegrationConfig(base_url="http://vmlogs:9428")
        assert config.integration_id == ""


class TestLoadEnvIntegrations:
    """Tests for VictoriaLogs env-var loading in load_env_integrations."""

    def test_url_alone_loads_active_integration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VICTORIA_LOGS_URL", "http://vmlogs:9428")
        monkeypatch.delenv("VICTORIA_LOGS_TENANT_ID", raising=False)
        records = load_env_integrations()

        victoria = next((r for r in records if r["service"] == "victoria_logs"), None)
        assert victoria is not None
        assert victoria["id"] == "env-victoria_logs"
        assert victoria["status"] == "active"
        assert victoria["credentials"]["base_url"] == "http://vmlogs:9428"
        assert victoria["credentials"]["tenant_id"] is None

    def test_no_url_means_no_record(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VICTORIA_LOGS_URL", raising=False)
        records = load_env_integrations()

        assert all(r["service"] != "victoria_logs" for r in records)

    def test_tenant_id_propagates_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VICTORIA_LOGS_URL", "http://vmlogs:9428")
        monkeypatch.setenv("VICTORIA_LOGS_TENANT_ID", "acme")
        records = load_env_integrations()

        victoria = next((r for r in records if r["service"] == "victoria_logs"), None)
        assert victoria is not None
        assert victoria["credentials"]["tenant_id"] == "acme"


class TestClassifyServiceInstance:
    """Tests for _classify_service_instance — the DB-store classification path."""

    def test_classifies_victoria_logs_with_base_url(self) -> None:
        config, key = _classify_service_instance(
            key="victoria_logs",
            credentials={"base_url": "http://vmlogs:9428"},
            record_id="vl-prod",
        )
        assert key == "victoria_logs"
        assert config is not None
        assert config["base_url"] == "http://vmlogs:9428"
        assert config["integration_id"] == "vl-prod"

    def test_skips_when_base_url_missing(self) -> None:
        config, key = _classify_service_instance(
            key="victoria_logs",
            credentials={"base_url": ""},
            record_id="vl-broken",
        )
        assert config is None and key is None

    def test_classifies_with_tenant_id(self) -> None:
        # Records reaching ``_classify_service_instance`` have already been
        # normalized through the service-key map, so they always arrive under
        # the canonical key. This case verifies the tenant-id path round-trips
        # correctly through the classifier.
        config, key = _classify_service_instance(
            key="victoria_logs",
            credentials={"base_url": "http://vmlogs:9428", "tenant_id": "team-a"},
            record_id="vl-team-a",
        )
        assert key == "victoria_logs"
        assert config is not None
        assert config["tenant_id"] == "team-a"


class TestVerifyVictoriaLogs:
    """Tests for the verifier — focuses on the status contract."""

    def test_missing_when_base_url_absent(self) -> None:
        result = _verify_victoria_logs(source="local env", config={"base_url": ""})
        assert result["status"] == "missing"
        assert result["service"] == "victoria_logs"

    def test_passed_on_successful_query(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"_msg":"hello"}\n'
        mock_response.raise_for_status.return_value = None

        with patch("httpx.Client.get", return_value=mock_response):
            result = _verify_victoria_logs(
                source="local env",
                config={"base_url": "http://vmlogs:9428"},
            )

        assert result["status"] == "passed", (
            "Verifier must return status='passed' on success — "
            "verification_exit_code() checks for 'passed' specifically. "
            "Returning 'verified' or any other string silently fails the core gate."
        )
        assert "vmlogs:9428" in result["detail"]

    def test_failed_on_http_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "internal error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        with patch("httpx.Client.get", return_value=mock_response):
            result = _verify_victoria_logs(
                source="local env",
                config={"base_url": "http://vmlogs:9428"},
            )

        assert result["status"] == "failed"
        assert "500" in result["detail"]


class TestVictoriaLogsIntegrationCanonicalShape:
    """Sanity checks that the integration shows up where the runtime expects it."""

    def test_classified_config_dump_matches_pydantic_model(self) -> None:
        """The classifier's model_dump output should round-trip through the model."""
        config, _ = _classify_service_instance(
            key="victoria_logs",
            credentials={"base_url": "http://vmlogs:9428", "tenant_id": "x"},
            record_id="vl-1",
        )
        assert isinstance(config, dict)
        # Re-validate to confirm the dumped shape is acceptable to downstream
        # consumers that re-parse credentials through the model.
        roundtrip = VictoriaLogsIntegrationConfig.model_validate(config)
        assert roundtrip.base_url == "http://vmlogs:9428"
        assert roundtrip.tenant_id == "x"

    def test_effective_integrations_field_exists(self) -> None:
        """``victoria_logs`` must be a declared field on EffectiveIntegrations.

        EffectiveIntegrations uses ``extra='forbid'``; an undeclared ``victoria_logs``
        key would raise ValidationError when resolve_effective_integrations tries
        to populate it. This was a P1 in PR #663.
        """
        from app.integrations.models import EffectiveIntegrationEntry, EffectiveIntegrations

        entry = EffectiveIntegrationEntry(
            source="local env",
            config={"base_url": "http://vmlogs:9428"},
        )
        # Should not raise.
        effective: dict[str, Any] = {"victoria_logs": entry.model_dump()}
        EffectiveIntegrations.model_validate(effective)
