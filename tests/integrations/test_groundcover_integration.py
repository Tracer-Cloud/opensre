"""Config, catalog, env-loading, and verification tests for the groundcover provider."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from integrations.groundcover.config import GroundcoverIntegrationConfig
from integrations.groundcover.verifier import verify_groundcover as _verify_groundcover
from integrations.probes import ProbeResult
from integrations.verify import resolve_effective_integrations


def test_config_defaults_and_normalization() -> None:
    config = GroundcoverIntegrationConfig.model_validate(
        {"api_key": "Bearer tok", "mcp_url": "", "timezone": "", "tenant_uuid": " "}
    )
    assert config.api_key == "tok"  # Bearer prefix stripped
    assert config.mcp_url == "https://mcp.groundcover.com/api/mcp"
    assert config.timezone == "UTC"
    assert config.tenant_uuid == ""
    assert config.is_configured is True


def test_config_request_headers_only_include_configured_routing() -> None:
    minimal = GroundcoverIntegrationConfig.model_validate({"api_key": "tok"})
    assert minimal.request_headers == {
        "Authorization": "Bearer tok",
        "X-Timezone": "UTC",
    }
    routed = GroundcoverIntegrationConfig.model_validate(
        {"api_key": "tok", "tenant_uuid": "t1", "backend_id": "b1"}
    )
    assert routed.request_headers["X-Tenant-UUID"] == "t1"
    assert routed.request_headers["X-Backend-Id"] == "b1"


def test_config_rejects_unknown_field_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="tenant_uuid"):
        GroundcoverIntegrationConfig.model_validate({"api_key": "tok", "tenant_id": "t1"})


def test_config_rejects_non_https_url() -> None:
    with pytest.raises(ValidationError, match="https"):
        GroundcoverIntegrationConfig.model_validate(
            {"api_key": "tok", "mcp_url": "http://mcp.groundcover.com/api/mcp"}
        )


def test_config_allows_loopback_http_for_tests() -> None:
    config = GroundcoverIntegrationConfig.model_validate(
        {"api_key": "tok", "mcp_url": "http://127.0.0.1:8080/api/mcp"}
    )
    assert config.mcp_url == "http://127.0.0.1:8080/api/mcp"


def test_env_single_instance_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("GROUNDCOVER_API_KEY", "env-tok")
    monkeypatch.setenv("GROUNDCOVER_TENANT_UUID", "t1")

    effective = resolve_effective_integrations()

    assert effective["groundcover"]["source"] == "local env"
    config = effective["groundcover"]["config"]
    assert config["api_key"] == "env-tok"
    assert config["tenant_uuid"] == "t1"
    assert config["mcp_url"] == "https://mcp.groundcover.com/api/mcp"


def test_env_token_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.delenv("GROUNDCOVER_API_KEY", raising=False)
    monkeypatch.setenv("GROUNDCOVER_MCP_TOKEN", "alias-tok")

    effective = resolve_effective_integrations()

    assert effective["groundcover"]["config"]["api_key"] == "alias-tok"


def test_env_multi_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.catalog import load_env_integrations

    monkeypatch.delenv("GROUNDCOVER_API_KEY", raising=False)
    monkeypatch.delenv("GROUNDCOVER_MCP_TOKEN", raising=False)
    monkeypatch.setenv(
        "GROUNDCOVER_INSTANCES",
        '[{"name":"prod","api_key":"k1","tenant_uuid":"t1"},'
        '{"name":"staging","api_key":"k2","tenant_uuid":"t2"}]',
    )

    records = load_env_integrations()
    groundcover_records = [r for r in records if r.get("service") == "groundcover"]
    assert len(groundcover_records) == 1
    instances = groundcover_records[0]["instances"]
    assert [i["name"] for i in instances] == ["prod", "staging"]
    assert instances[0]["credentials"]["api_key"] == "k1"


def test_verify_reports_probe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.groundcover.client import GroundcoverClient

    monkeypatch.setattr(
        GroundcoverClient,
        "probe_access",
        lambda _self: ProbeResult.failed("Could not connect to groundcover MCP: timeout"),
    )

    result = _verify_groundcover("local env", {"api_key": "tok"})

    assert result["status"] == "failed"
    assert "connect" in result["detail"].lower()


def test_verify_missing_when_no_token() -> None:
    result = _verify_groundcover("local store", {"api_key": "", "integration_id": "gc-local"})
    assert result["status"] == "missing"
    assert "Missing groundcover API key" in result["detail"]


def test_verify_never_leaks_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.groundcover.client import GroundcoverClient

    def _raise(_self: GroundcoverClient) -> ProbeResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(GroundcoverClient, "probe_access", _raise)

    result = _verify_groundcover("local env", {"api_key": "super-secret-token"})

    assert result["status"] == "failed"
    assert "super-secret-token" not in result["detail"]
