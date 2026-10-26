"""Unit tests for the Sentry MCP integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from integrations.catalog import classify_integrations as _classify_integrations
from integrations.sentry_mcp import (
    DEFAULT_SENTRY_MCP_URL,
    SentryMCPConfig,
    build_sentry_mcp_config,
    describe_sentry_mcp_error,
    sentry_mcp_config_from_env,
    sentry_mcp_runtime_unavailable_reason,
    validate_sentry_mcp_config,
)

# ---------------------------------------------------------------------------
# SentryMCPConfig
# ---------------------------------------------------------------------------


class TestSentryMCPConfig:
    def test_defaults_to_hosted_streamable_http(self) -> None:
        config = SentryMCPConfig()
        assert config.mode == "streamable-http"
        assert config.url == DEFAULT_SENTRY_MCP_URL
        assert config.is_configured is True

    def test_streamable_http_requires_url(self) -> None:
        with pytest.raises(ValidationError, match="requires a non-empty url"):
            SentryMCPConfig(mode="streamable-http", url="")

    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValidationError, match="requires a non-empty command"):
            SentryMCPConfig(mode="stdio", command="", url="")

    def test_url_trailing_slash_stripped(self) -> None:
        config = SentryMCPConfig(url="https://mcp.sentry.dev/mcp/")
        assert config.url == "https://mcp.sentry.dev/mcp"

    def test_bearer_prefix_stripped_from_token(self) -> None:
        config = SentryMCPConfig(auth_token="Bearer sntrytok_secret")
        assert config.auth_token == "sntrytok_secret"

    def test_skills_normalized_from_string(self) -> None:
        config = SentryMCPConfig(skills="inspect, seer")
        assert config.skills == ("inspect", "seer")

    def test_request_headers_include_auth(self) -> None:
        config = SentryMCPConfig(auth_token="sntrytok_secret")
        headers = config.request_headers
        assert headers["Authorization"] == "Bearer sntrytok_secret"

    def test_request_headers_omit_auth_without_token(self) -> None:
        config = SentryMCPConfig(auth_token="")
        assert "Authorization" not in config.request_headers

    def test_session_url_matches_url(self) -> None:
        config = SentryMCPConfig()
        assert config.session_url == DEFAULT_SENTRY_MCP_URL


# ---------------------------------------------------------------------------
# Env loading / runtime gating
# ---------------------------------------------------------------------------


class TestEnvLoading:
    def test_returns_none_without_token_for_hosted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "SENTRY_MCP_MODE",
            "SENTRY_MCP_URL",
            "SENTRY_MCP_COMMAND",
            "SENTRY_MCP_AUTH_TOKEN",
            "SENTRY_MCP_ARGS",
        ):
            monkeypatch.delenv(var, raising=False)
        assert sentry_mcp_config_from_env() is None

    def test_loads_hosted_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_MCP_AUTH_TOKEN", "sntrytok_secret")
        monkeypatch.delenv("SENTRY_MCP_URL", raising=False)
        monkeypatch.delenv("SENTRY_MCP_MODE", raising=False)
        config = sentry_mcp_config_from_env()
        assert config is not None
        assert config.url == DEFAULT_SENTRY_MCP_URL
        assert config.auth_token == "sntrytok_secret"

    def test_loads_stdio_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_MCP_MODE", "stdio")
        monkeypatch.setenv("SENTRY_MCP_COMMAND", "npx")
        monkeypatch.setenv("SENTRY_MCP_ARGS", "@sentry/mcp-server@latest")
        monkeypatch.delenv("SENTRY_MCP_URL", raising=False)
        config = sentry_mcp_config_from_env()
        assert config is not None
        assert config.mode == "stdio"
        assert config.command == "npx"
        assert config.args == ("@sentry/mcp-server@latest",)

    def test_runtime_reason_requires_token_for_hosted(self) -> None:
        config = build_sentry_mcp_config({"url": DEFAULT_SENTRY_MCP_URL, "auth_token": ""})
        reason = sentry_mcp_runtime_unavailable_reason(config)
        assert reason is not None
        assert "user auth token" in reason

    def test_runtime_reason_ok_with_token(self) -> None:
        config = build_sentry_mcp_config({"auth_token": "sntrytok_secret"})
        assert sentry_mcp_runtime_unavailable_reason(config) is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validation_fails_without_token(self) -> None:
        config = build_sentry_mcp_config({"auth_token": ""})
        result = validate_sentry_mcp_config(config)
        assert result.ok is False
        assert "user auth token" in result.detail

    def test_validation_passes_when_tools_listed(self) -> None:
        config = build_sentry_mcp_config({"auth_token": "sntrytok_secret"})
        fake_tools = [
            {"name": "get_issue_details", "description": "Issue", "input_schema": {}},
            {"name": "analyze_issue_with_seer", "description": "Seer", "input_schema": {}},
        ]
        with patch(
            "integrations.sentry_mcp.list_sentry_mcp_tools",
            return_value=fake_tools,
        ):
            result = validate_sentry_mcp_config(config)
        assert result.ok is True
        assert result.tool_names == ("analyze_issue_with_seer", "get_issue_details")
        assert "discovered 2 tool(s)" in result.detail

    def test_validation_fails_when_no_tools(self) -> None:
        config = build_sentry_mcp_config({"auth_token": "sntrytok_secret"})
        with patch(
            "integrations.sentry_mcp.list_sentry_mcp_tools",
            return_value=[],
        ):
            result = validate_sentry_mcp_config(config)
        assert result.ok is False
        assert "no tools" in result.detail

    def test_validation_handles_exception(self) -> None:
        config = build_sentry_mcp_config({"auth_token": "sntrytok_secret"})
        with patch(
            "integrations.sentry_mcp.list_sentry_mcp_tools",
            side_effect=RuntimeError("boom"),
        ):
            result = validate_sentry_mcp_config(config)
        assert result.ok is False
        assert "validation failed" in result.detail


def test_describe_error_includes_auth_hint() -> None:
    import httpx

    config = build_sentry_mcp_config({"auth_token": "sntrytok_secret"})
    request = httpx.Request("GET", DEFAULT_SENTRY_MCP_URL)
    response = httpx.Response(401, request=request)
    err = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    detail = describe_sentry_mcp_error(err, config)
    assert "Authentication failed" in detail


# ---------------------------------------------------------------------------
# Catalog classification
# ---------------------------------------------------------------------------


def test_classify_sentry_mcp_credentials() -> None:
    records = [
        {
            "id": "sentry-mcp-prod",
            "service": "sentry_mcp",
            "status": "active",
            "credentials": {
                "url": "https://mcp.sentry.dev/mcp",
                "mode": "streamable-http",
                "auth_token": "sntrytok_secret",
                "organization_slug": "my-org",
            },
        }
    ]
    from core.tool import availability_view

    resolved = _classify_integrations(records)
    assert "sentry_mcp" in resolved
    assert resolved["sentry_mcp"].auth_token == "sntrytok_secret"
    assert resolved["sentry_mcp"].organization_slug == "my-org"
    # connection_verified is set at the tool-availability boundary
    view = availability_view(resolved)
    assert view["sentry_mcp"]["connection_verified"] is True
