from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.integrations.github_mcp import build_github_mcp_config
from app.integrations.models import (
    AWSIntegrationConfig,
    CoralogixIntegrationConfig,
    HoneycombIntegrationConfig,
    SlackWebhookConfig,
    TracerIntegrationConfig,
)
from app.integrations.sentry import build_sentry_config
from app.services.datadog.client import DatadogConfig
from app.services.grafana.config import GrafanaAccountConfig


def test_sentry_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="organiztion_slug.*organization_slug"):
        build_sentry_config(
            {
                "base_url": "https://sentry.io",
                "organiztion_slug": "demo-org",
                "auth_token": "sntrys_test",
            }
        )


def test_github_mcp_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="toolset.*toolsets"):
        build_github_mcp_config(
            {
                "url": "https://api.githubcopilot.com/mcp/",
                "mode": "streamable-http",
                "auth_token": "ghp_test",
                "toolset": ["repos"],
            }
        )


def test_github_mcp_stdio_requires_command() -> None:
    with pytest.raises(ValidationError, match="requires a non-empty command"):
        build_github_mcp_config({"mode": "stdio"})


def test_datadog_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="siet.*site"):
        DatadogConfig.model_validate(
            {
                "api_key": "dd-api",
                "app_key": "dd-app",
                "siet": "datadoghq.com",
            }
        )


def test_honeycomb_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="datset.*dataset"):
        HoneycombIntegrationConfig.model_validate(
            {
                "api_key": "hny_test",
                "datset": "prod-api",
            }
        )


def test_coralogix_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="base_ul.*base_url"):
        CoralogixIntegrationConfig.model_validate(
            {
                "api_key": "cx_test",
                "base_ul": "https://api.coralogix.com",
            }
        )


def test_grafana_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="instnce_url.*instance_url"):
        GrafanaAccountConfig.model_validate(
            {
                "account_id": "grafana-1",
                "instance_url": "https://grafana.example.com",
                "read_token": "token",
                "instnce_url": "https://grafana.example.com",
            }
        )


def test_aws_config_requires_auth_method() -> None:
    with pytest.raises(ValidationError, match="requires either role_arn or credentials"):
        AWSIntegrationConfig.model_validate({"region": "us-east-1"})


def test_slack_config_rejects_non_slack_host() -> None:
    with pytest.raises(ValidationError, match="Slack webhook host must be a Slack domain"):
        SlackWebhookConfig.model_validate({"webhook_url": "https://example.com/webhook"})


def test_tracer_config_strips_bearer_prefix() -> None:
    config = TracerIntegrationConfig.model_validate(
        {
            "base_url": "https://app.tracer.cloud",
            "jwt_token": "Bearer test-token",
        }
    )

    assert config.jwt_token == "test-token"
