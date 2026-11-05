"""Unit tests for the Gitee MCP integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from integrations.catalog import classify_integrations
from integrations.gitee import (
    DEFAULT_GITEE_MCP_URL,
    DEFAULT_GITEE_READ_TOOLS,
    GiteeMCPConfig,
    build_gitee_mcp_config,
    classify,
    gitee_mcp_config_from_env,
    gitee_mcp_runtime_unavailable_reason,
    validate_gitee_mcp_config,
)
from integrations.registry import resolve_management_service

_ENV_VARS = (
    "GITEE_MCP_MODE",
    "GITEE_MCP_URL",
    "GITEE_MCP_COMMAND",
    "GITEE_MCP_ARGS",
    "GITEE_MCP_AUTH_TOKEN",
    "GITEE_ACCESS_TOKEN",
    "GITEE_MCP_ENABLED_TOOLS",
    "GITEE_MCP_TIMEOUT_SECONDS",
)


class TestGiteeMCPConfig:
    def test_defaults_to_official_remote_endpoint(self) -> None:
        config = GiteeMCPConfig()
        assert config.mode == "streamable-http"
        assert config.url == DEFAULT_GITEE_MCP_URL
        assert config.enabled_tools == DEFAULT_GITEE_READ_TOOLS
        assert config.is_configured is True

    def test_streamable_http_requires_url(self) -> None:
        with pytest.raises(ValidationError, match="requires a non-empty url"):
            GiteeMCPConfig(mode="streamable-http", url="")

    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValidationError, match="requires a non-empty command"):
            GiteeMCPConfig(mode="stdio", command="", url="")

    def test_url_trailing_slash_stripped(self) -> None:
        config = GiteeMCPConfig(url="https://api.gitee.com/mcp/")
        assert config.url == "https://api.gitee.com/mcp"

    def test_bearer_prefix_stripped_from_token(self) -> None:
        config = GiteeMCPConfig(auth_token="Bearer gitee_secret")
        assert config.auth_token == "gitee_secret"

    def test_enabled_tools_accepts_csv(self) -> None:
        config = GiteeMCPConfig(enabled_tools="get_file_content, search_files_by_content")
        assert config.enabled_tools == ("get_file_content", "search_files_by_content")

    def test_empty_enabled_tools_falls_back_to_read_only_allowlist(self) -> None:
        config = GiteeMCPConfig(enabled_tools="")
        assert config.enabled_tools == DEFAULT_GITEE_READ_TOOLS

    def test_request_headers_include_bearer_and_allowlist(self) -> None:
        config = GiteeMCPConfig(auth_token="gitee_secret")
        headers = config.request_headers
        assert headers["Authorization"] == "Bearer gitee_secret"
        assert "get_file_content" in headers["X-MCP-Enabled-Tools"]

    def test_stdio_omits_allowlist_header(self) -> None:
        config = GiteeMCPConfig(mode="stdio", command="npx", url="")
        assert "X-MCP-Enabled-Tools" not in config.request_headers


class TestEnvLoading:
    def test_returns_none_without_token_for_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in _ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        assert gitee_mcp_config_from_env() is None

    def test_loads_remote_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in _ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GITEE_MCP_AUTH_TOKEN", "gitee_secret")
        config = gitee_mcp_config_from_env()
        assert config is not None
        assert config.url == DEFAULT_GITEE_MCP_URL
        assert config.auth_token == "gitee_secret"

    def test_falls_back_to_gitee_access_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in _ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GITEE_ACCESS_TOKEN", "gitee_secret")
        config = gitee_mcp_config_from_env()
        assert config is not None
        assert config.auth_token == "gitee_secret"

    def test_loads_stdio_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in _ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GITEE_MCP_MODE", "stdio")
        monkeypatch.setenv("GITEE_MCP_COMMAND", "npx")
        monkeypatch.setenv("GITEE_MCP_ARGS", "-y @gitee/mcp-gitee@latest")
        config = gitee_mcp_config_from_env()
        assert config is not None
        assert config.mode == "stdio"
        assert config.command == "npx"
        assert config.args == ("-y", "@gitee/mcp-gitee@latest")

    def test_runtime_reason_requires_token_for_remote(self) -> None:
        config = build_gitee_mcp_config({"url": DEFAULT_GITEE_MCP_URL, "auth_token": ""})
        reason = gitee_mcp_runtime_unavailable_reason(config)
        assert reason is not None
        assert "personal access token" in reason

    def test_runtime_reason_ok_with_token(self) -> None:
        config = build_gitee_mcp_config({"auth_token": "gitee_secret"})
        assert gitee_mcp_runtime_unavailable_reason(config) is None


class TestValidation:
    def test_validation_fails_without_token(self) -> None:
        config = build_gitee_mcp_config({"auth_token": ""})
        result = validate_gitee_mcp_config(config)
        assert result.ok is False
        assert "personal access token" in result.detail

    def test_validation_requires_the_code_read_tools(self) -> None:
        config = build_gitee_mcp_config({"auth_token": "gitee_secret"})
        fake_tools = [
            {"name": "get_file_content", "description": "File", "input_schema": {}},
        ]
        with patch(
            "integrations.gitee.mcp.list_gitee_mcp_tools",
            return_value=fake_tools,
        ):
            result = validate_gitee_mcp_config(config)
        assert result.ok is False
        assert "search_files_by_content" in result.detail

    def test_validation_passes_when_read_tools_are_listed(self) -> None:
        config = build_gitee_mcp_config({"auth_token": "gitee_secret"})
        fake_tools = [
            {"name": "get_file_content", "description": "File", "input_schema": {}},
            {"name": "search_files_by_content", "description": "Search", "input_schema": {}},
        ]
        with patch(
            "integrations.gitee.mcp.list_gitee_mcp_tools",
            return_value=fake_tools,
        ):
            result = validate_gitee_mcp_config(config)
        assert result.ok is True
        assert result.tool_names == ("get_file_content", "search_files_by_content")
        assert "discovered 2 tool(s)" in result.detail


class TestClassifyAndWiring:
    def test_classify_returns_canonical_service(self) -> None:
        config, service = classify({"auth_token": "gitee_secret"}, "record-1")
        assert service == "gitee_mcp"
        assert config is not None
        assert config.integration_id == "record-1"

    def test_classify_skips_unconfigured_record(self) -> None:
        config, service = classify({}, "record-1")
        assert config is None
        assert service is None

    def test_gitee_alias_resolves_to_gitee_mcp(self) -> None:
        assert resolve_management_service("gitee") == "gitee_mcp"

    def test_environment_record_is_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITEE_MCP_AUTH_TOKEN", "gitee_secret")
        resolved = classify_integrations(
            [
                {
                    "service": "gitee_mcp",
                    "status": "active",
                    "credentials": {"auth_token": "gitee_secret"},
                }
            ]
        )
        assert resolved["gitee_mcp"]["auth_token"] == "gitee_secret"
