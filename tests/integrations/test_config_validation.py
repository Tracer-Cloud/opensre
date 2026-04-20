from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.integrations.github_mcp import (
    _remote_github_mcp_session_url,
    build_github_mcp_config,
)
from app.integrations.models import (
    AWSIntegrationConfig,
    BetterStackIntegrationConfig,
    CoralogixIntegrationConfig,
    HoneycombIntegrationConfig,
    SlackWebhookConfig,
    TracerIntegrationConfig,
)
from app.integrations.sentry import build_sentry_config
from app.services.datadog.client import DatadogConfig
from app.services.grafana.config import GrafanaAccountConfig


def test_betterstack_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="query_endpont.*query_endpoint"):
        BetterStackIntegrationConfig(
            query_endpont="https://x",  # type: ignore[call-arg]
            username="u",
        )


def test_betterstack_config_strips_trailing_slash_and_whitespace() -> None:
    cfg = BetterStackIntegrationConfig(
        query_endpoint="  https://eu-nbg-2-connect.betterstackdata.com/  ",
        username="  user  ",
    )
    assert cfg.query_endpoint == "https://eu-nbg-2-connect.betterstackdata.com"
    assert cfg.username == "user"


def test_betterstack_config_sources_from_comma_string() -> None:
    cfg = BetterStackIntegrationConfig(
        query_endpoint="https://x",
        username="u",
        sources="t1_myapp, t2_gateway",  # type: ignore[arg-type]
    )
    assert cfg.sources == ["t1_myapp", "t2_gateway"]


def test_sentry_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="organiztion_slug.*organization_slug"):
        build_sentry_config({
            "base_url": "https://sentry.io",
            "organiztion_slug": "demo-org",
            "auth_token": "sntrys_test",
        })


def test_github_mcp_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="toolset.*toolsets"):
        build_github_mcp_config({
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
            "toolset": ["repos"],
        })


def test_github_mcp_stdio_requires_command() -> None:
    with pytest.raises(ValidationError, match="requires a non-empty command"):
        build_github_mcp_config({"mode": "stdio"})


def test_github_mcp_remote_request_headers_include_x_mcp_toolsets() -> None:
    """Explicit Copilot MCP paths use X-MCP-Toolsets to merge toolsets (remote-server.md)."""
    cfg = build_github_mcp_config({
        "url": "https://api.githubcopilot.com/mcp/x/issues",
        "mode": "streamable-http",
        "auth_token": "ghp_test",
        "toolsets": ["repos", "issues"],
    })
    assert cfg.request_headers["X-MCP-Toolsets"] == "repos,issues"


def test_github_mcp_generic_copilot_root_omits_x_mcp_toolsets() -> None:
    """Generic /mcp uses rewritten /x/all/readonly; subset header would hide search tools."""
    cfg = build_github_mcp_config({
        "url": "https://api.githubcopilot.com/mcp/",
        "mode": "streamable-http",
        "auth_token": "ghp_test",
        "toolsets": ["repos", "issues"],
    })
    assert "X-MCP-Toolsets" not in cfg.request_headers


def test_github_mcp_stdio_omits_x_mcp_toolsets_header() -> None:
    cfg = build_github_mcp_config({
        "mode": "stdio",
        "command": "github-mcp-server",
        "toolsets": ["repos"],
    })
    assert "X-MCP-Toolsets" not in cfg.request_headers


def test_remote_github_mcp_root_url_rewrites_to_x_all_readonly() -> None:
    assert (
        _remote_github_mcp_session_url("https://api.githubcopilot.com/mcp/")
        == "https://api.githubcopilot.com/mcp/x/all/readonly"
    )
    assert (
        _remote_github_mcp_session_url("https://api.githubcopilot.com/mcp")
        == "https://api.githubcopilot.com/mcp/x/all/readonly"
    )


def test_remote_github_mcp_explicit_paths_not_rewritten() -> None:
    url = "https://api.githubcopilot.com/mcp/x/repos"
    assert _remote_github_mcp_session_url(url) == url
    assert _remote_github_mcp_session_url("https://api.githubcopilot.com/mcp/readonly") == (
        "https://api.githubcopilot.com/mcp/readonly"
    )


def test_remote_github_mcp_other_hosts_unchanged() -> None:
    url = "https://example.com/mcp/"
    assert _remote_github_mcp_session_url(url) == url


def test_github_mcp_custom_headers_can_override_x_mcp_toolsets() -> None:
    cfg = build_github_mcp_config({
        "url": "https://api.githubcopilot.com/mcp/",
        "mode": "streamable-http",
        "toolsets": ["repos"],
        "headers": {"X-MCP-Toolsets": "repos,pull_requests"},
    })
    assert cfg.request_headers["X-MCP-Toolsets"] == "repos,pull_requests"


def test_datadog_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="siet.*site"):
        DatadogConfig.model_validate({
            "api_key": "dd-api",
            "app_key": "dd-app",
            "siet": "datadoghq.com",
        })


def test_honeycomb_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="datset.*dataset"):
        HoneycombIntegrationConfig.model_validate({
            "api_key": "hny_test",
            "datset": "prod-api",
        })


def test_coralogix_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="base_ul.*base_url"):
        CoralogixIntegrationConfig.model_validate({
            "api_key": "cx_test",
            "base_ul": "https://api.coralogix.com",
        })


def test_grafana_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="instnce_url.*instance_url"):
        GrafanaAccountConfig.model_validate({
            "account_id": "grafana-1",
            "instance_url": "https://grafana.example.com",
            "read_token": "token",
            "instnce_url": "https://grafana.example.com",
        })


def test_aws_config_requires_auth_method() -> None:
    with pytest.raises(ValidationError, match="requires either role_arn or credentials"):
        AWSIntegrationConfig.model_validate({"region": "us-east-1"})


def test_slack_config_rejects_non_slack_host() -> None:
    with pytest.raises(ValidationError, match="Slack webhook host must be a Slack domain"):
        SlackWebhookConfig.model_validate({"webhook_url": "https://example.com/webhook"})


def test_tracer_config_strips_bearer_prefix() -> None:
    config = TracerIntegrationConfig.model_validate({
        "base_url": "https://app.tracer.cloud",
        "jwt_token": "Bearer test-token",
    })

    assert config.jwt_token == "test-token"


def test_posthog_config_rejects_unknown_fields_with_suggestion() -> None:
    from app.integrations.posthog import build_posthog_config

    with pytest.raises(ValidationError, match="proejct_id.*project_id"):
        build_posthog_config({
            "personal_api_key": "phx_test",
            "proejct_id": "12345",
        })
