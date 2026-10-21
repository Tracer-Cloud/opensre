"""Stable import surface for onboarding integration health validators."""

from __future__ import annotations

from surfaces.cli.wizard.integration_validators.alerting import (
    validate_alertmanager_integration,
    validate_betterstack_integration,
)
from surfaces.cli.wizard.integration_validators.aws import validate_aws_integration
from surfaces.cli.wizard.integration_validators.http_probe_validators import (
    validate_discord_bot,
    validate_jira_integration,
    validate_rocketchat,
    validate_rocketchat_webhook,
    validate_servicenow_integration,
    validate_slack_webhook,
)
from surfaces.cli.wizard.integration_validators.mcp_validators import (
    validate_github_mcp_integration,
    validate_posthog_mcp_integration,
    validate_sentry_mcp_integration,
)
from surfaces.cli.wizard.integration_validators.observability import (
    validate_grafana_integration,
    validate_opensearch_integration,
    validate_splunk_integration,
)
from surfaces.cli.wizard.integration_validators.productivity import (
    validate_google_docs_integration,
)
from surfaces.cli.wizard.integration_validators.shared import IntegrationHealthResult

__all__ = [
    "IntegrationHealthResult",
    "validate_alertmanager_integration",
    "validate_aws_integration",
    "validate_betterstack_integration",
    "validate_discord_bot",
    "validate_github_mcp_integration",
    "validate_google_docs_integration",
    "validate_grafana_integration",
    "validate_jira_integration",
    "validate_opensearch_integration",
    "validate_posthog_mcp_integration",
    "validate_rocketchat",
    "validate_rocketchat_webhook",
    "validate_sentry_mcp_integration",
    "validate_servicenow_integration",
    "validate_slack_webhook",
    "validate_splunk_integration",
]
