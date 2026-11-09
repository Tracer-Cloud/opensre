"""Unit tests for the PostHog MCP integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from integrations.catalog import classify_integrations as _classify_integrations
from integrations.posthog_mcp import (
    DEFAULT_POSTHOG_MCP_URL,
    PostHogMCPConfig,
    build_posthog_mcp_config,
    describe_posthog_mcp_error,
    posthog_mcp_config_from_env,
    posthog_mcp_runtime_unavailable_reason,
    validate_posthog_mcp_config,
)

# ---------------------------------------------------------------------------
# PostHogMCPConfig
# ---------------------------------------------------------------------------


class TestPostHogMCPConfig:
    def test_defaults_to_hosted_streamable_http(self) -> None:
        config = PostHogMCPConfig()
        assert config.mode == "streamable-http"
        assert config.url == DEFAULT_POSTHOG_MCP_URL
        assert config.is_configured is True
        assert config.read_only is True

    def test_streamable_http_requires_url(self) -> None:
        with pytest.raises(ValidationError, match="requires a non-empty url"):
            PostHogMCPConfig(mode="streamable-http", url="")

    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValidationError, match="requires a non-empty command"):
            PostHogMCPConfig(mode="stdio", command="", url="")

    def test_url_trailing_slash_stripped(self) -> None:
        config = PostHogMCPConfig(url="https://mcp.posthog.com/mcp/")
        assert config.url == "https://mcp.posthog.com/mcp"

    def test_mode_mcp_alias_maps_to_streamable_http(self) -> None:
        config = PostHogMCPConfig(mode="mcp")
        assert config.mode == "streamable-http"

    @pytest.mark.parametrize("alias", ["default", "http", "https", "streamable_http"])
    def test_mode_generic_aliases_map_to_streamable_http(self, alias: str) -> None:
        config = PostHogMCPConfig(mode=alias)
        assert config.mode == "streamable-http"

    def test_bearer_prefix_stripped_from_token(self) -> None:
        config = PostHogMCPConfig(auth_token="Bearer phx_secret")
        assert config.auth_token == "phx_secret"

    def test_features_normalized_from_string(self) -> None:
        config = PostHogMCPConfig(features="flags, error-tracking")
        assert config.features == ("flags", "error-tracking")

    def test_request_headers_include_auth_and_scoping(self) -> None:
        config = PostHogMCPConfig(
            auth_token="phx_secret",
            organization_id="org-1",
            project_id="proj-9",
        )
        headers = config.request_headers
        assert headers["Authorization"] == "Bearer phx_secret"
        assert headers["x-posthog-organization-id"] == "org-1"
        assert headers["x-posthog-project-id"] == "proj-9"
        assert headers["x-posthog-read-only"] == "true"

    def test_read_only_header_omitted_when_disabled(self) -> None:
        config = PostHogMCPConfig(auth_token="phx_secret", read_only=False)
        assert "x-posthog-read-only" not in config.request_headers

    def test_session_url_merges_features_query(self) -> None:
        config = PostHogMCPConfig(features=["flags", "errors"])
        assert config.session_url == f"{DEFAULT_POSTHOG_MCP_URL}?features=flags%2Cerrors"

    def test_session_url_unchanged_without_features(self) -> None:
        config = PostHogMCPConfig()
        assert config.session_url == DEFAULT_POSTHOG_MCP_URL


# ---------------------------------------------------------------------------
# Env loading / runtime gating
# ---------------------------------------------------------------------------


class TestEnvLoading:
    def test_returns_none_without_token_for_hosted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "POSTHOG_MCP_MODE",
            "POSTHOG_MCP_URL",
            "POSTHOG_MCP_COMMAND",
            "POSTHOG_MCP_AUTH_TOKEN",
            "POSTHOG_MCP_ARGS",
            "POSTHOG_MCP_READ_ONLY",
        ):
            monkeypatch.delenv(var, raising=False)
        assert posthog_mcp_config_from_env() is None

    def test_loads_hosted_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTHOG_MCP_AUTH_TOKEN", "phx_secret")
        monkeypatch.delenv("POSTHOG_MCP_URL", raising=False)
        monkeypatch.delenv("POSTHOG_MCP_MODE", raising=False)
        monkeypatch.delenv("POSTHOG_MCP_READ_ONLY", raising=False)
        config = posthog_mcp_config_from_env()
        assert config is not None
        assert config.url == DEFAULT_POSTHOG_MCP_URL
        assert config.auth_token == "phx_secret"
        assert config.read_only is True

    def test_read_only_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTHOG_MCP_AUTH_TOKEN", "phx_secret")
        monkeypatch.setenv("POSTHOG_MCP_READ_ONLY", "false")
        config = posthog_mcp_config_from_env()
        assert config is not None
        assert config.read_only is False

    def test_runtime_reason_requires_token_for_hosted(self) -> None:
        config = build_posthog_mcp_config({"url": DEFAULT_POSTHOG_MCP_URL, "auth_token": ""})
        reason = posthog_mcp_runtime_unavailable_reason(config)
        assert reason is not None
        assert "personal API key" in reason

    def test_runtime_reason_ok_with_token(self) -> None:
        config = build_posthog_mcp_config({"auth_token": "phx_secret"})
        assert posthog_mcp_runtime_unavailable_reason(config) is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validation_fails_without_token(self) -> None:
        config = build_posthog_mcp_config({"auth_token": ""})
        result = validate_posthog_mcp_config(config)
        assert result.ok is False
        assert "personal API key" in result.detail

    def test_validation_passes_when_tools_listed(self) -> None:
        config = build_posthog_mcp_config({"auth_token": "phx_secret"})
        fake_tools = [
            {"name": "query-run", "description": "Run HogQL", "input_schema": {}},
            {"name": "feature-flag-list", "description": "List flags", "input_schema": {}},
        ]
        with patch(
            "integrations.posthog_mcp.list_posthog_mcp_tools",
            return_value=fake_tools,
        ):
            result = validate_posthog_mcp_config(config)
        assert result.ok is True
        assert result.tool_names == ("feature-flag-list", "query-run")
        assert "discovered 2 tool(s)" in result.detail

    def test_validation_fails_when_no_tools(self) -> None:
        config = build_posthog_mcp_config({"auth_token": "phx_secret"})
        with patch(
            "integrations.posthog_mcp.list_posthog_mcp_tools",
            return_value=[],
        ):
            result = validate_posthog_mcp_config(config)
        assert result.ok is False
        assert "no tools" in result.detail

    def test_validation_handles_exception(self) -> None:
        config = build_posthog_mcp_config({"auth_token": "phx_secret"})
        with patch(
            "integrations.posthog_mcp.list_posthog_mcp_tools",
            side_effect=RuntimeError("boom"),
        ):
            result = validate_posthog_mcp_config(config)
        assert result.ok is False
        assert "validation failed" in result.detail


def test_describe_error_includes_auth_hint() -> None:
    import httpx

    config = build_posthog_mcp_config({"auth_token": "phx_secret"})
    request = httpx.Request("GET", DEFAULT_POSTHOG_MCP_URL)
    response = httpx.Response(401, request=request)
    err = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    detail = describe_posthog_mcp_error(err, config)
    assert "Authentication failed" in detail


# ---------------------------------------------------------------------------
# Catalog classification
# ---------------------------------------------------------------------------


def test_classify_posthog_mcp_credentials() -> None:
    records = [
        {
            "id": "posthog-mcp-prod",
            "service": "posthog_mcp",
            "status": "active",
            "credentials": {
                "url": "https://mcp.posthog.com/mcp",
                "mode": "streamable-http",
                "auth_token": "phx_secret",
                "project_id": "12345",
            },
        }
    ]
    from core.tool import availability_view

    resolved = _classify_integrations(records)
    assert "posthog_mcp" in resolved
    assert resolved["posthog_mcp"].auth_token == "phx_secret"
    assert resolved["posthog_mcp"].project_id == "12345"
    # connection_verified is set at the tool-availability boundary
    view = availability_view(resolved)
    assert view["posthog_mcp"]["connection_verified"] is True
