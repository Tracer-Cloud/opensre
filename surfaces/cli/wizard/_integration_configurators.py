"""Integration configurator dispatcher for the wizard onboarding flow."""

from __future__ import annotations

from infrastructure.terminal.theme import SECONDARY, WARNING
from surfaces.cli.wizard.components import choose, console, step
from surfaces.cli.wizard.configurators.alerting import (
    _configure_alertmanager,
    _configure_betterstack,
    _configure_incident_io,
    _configure_opsgenie,
    _configure_pagerduty,
)
from surfaces.cli.wizard.configurators.aws import _configure_aws
from surfaces.cli.wizard.configurators.chat_notifications import (
    _configure_buzz,
    _configure_discord,
    _configure_rocketchat,
    _configure_slack,
    _configure_telegram,
)
from surfaces.cli.wizard.configurators.dagster import _configure_dagster
from surfaces.cli.wizard.configurators.github import (
    _configure_github_app,
)
from surfaces.cli.wizard.configurators.gitlab import _configure_gitlab
from surfaces.cli.wizard.configurators.jenkins import _configure_jenkins
from surfaces.cli.wizard.configurators.observability import (
    _configure_coralogix,
    _configure_datadog,
    _configure_grafana,
    _configure_grafana_local,
    _configure_honeycomb,
    _configure_new_relic,
    _configure_opensearch,
    _configure_splunk,
    _configure_tempo,
)
from surfaces.cli.wizard.configurators.posthog import _configure_posthog, _configure_posthog_mcp
from surfaces.cli.wizard.configurators.productivity import (
    _configure_google_docs,
    _configure_jira,
    _configure_servicenow,
)
from surfaces.cli.wizard.configurators.sentry import _configure_sentry, _configure_sentry_mcp
from surfaces.cli.wizard.configurators.vercel import _configure_vercel
from surfaces.cli.wizard.onboard_integrations import (
    ONBOARD_INTEGRATION_CHOICES,
    ONBOARD_INTEGRATION_GROUP_ORDER,
    ONBOARD_SKIP_CHOICE,
)

__all__ = [
    "_configure_selected_integrations",
]


def _configure_selected_integrations() -> tuple[list[str], str | None]:
    """Configure one integration, or skip."""
    configured: list[str] = []
    last_env_path: str | None = None

    console.print(
        f"[{SECONDARY}]Pick one integration to wire up now, or skip this step "
        f"and come back later.[/]"
    )
    integration_choices = list(ONBOARD_INTEGRATION_CHOICES)
    selected_service = choose(
        "Choose an integration to configure",
        integration_choices,
        default="grafana_local",
        group_order=ONBOARD_INTEGRATION_GROUP_ORDER,
        trailing_choices=[ONBOARD_SKIP_CHOICE],
    )
    if selected_service == "skip":
        return configured, last_env_path

    handlers = {
        "grafana_local": _configure_grafana_local,
        "grafana": _configure_grafana,
        "datadog": _configure_datadog,
        "honeycomb": _configure_honeycomb,
        "coralogix": _configure_coralogix,
        "new_relic": _configure_new_relic,
        "slack": _configure_slack,
        "discord": _configure_discord,
        "telegram": _configure_telegram,
        "rocketchat": _configure_rocketchat,
        "buzz": _configure_buzz,
        "aws": _configure_aws,
        "github": _configure_github_app,
        "sentry": _configure_sentry,
        "gitlab": _configure_gitlab,
        "jenkins": _configure_jenkins,
        "google_docs": _configure_google_docs,
        "vercel": _configure_vercel,
        "dagster": _configure_dagster,
        "betterstack": _configure_betterstack,
        "jira": _configure_jira,
        "servicenow": _configure_servicenow,
        "alertmanager": _configure_alertmanager,
        "opsgenie": _configure_opsgenie,
        "pagerduty": _configure_pagerduty,
        "incident_io": _configure_incident_io,
        "posthog": _configure_posthog,
        "posthog_mcp": _configure_posthog_mcp,
        "sentry_mcp": _configure_sentry_mcp,
        "opensearch": _configure_opensearch,
        "splunk": _configure_splunk,
        "tempo": _configure_tempo,
    }
    _SERVICE_LABELS = {
        "grafana_local": "grafana local",
        "grafana": "grafana",
        "datadog": "datadog",
        "honeycomb": "honeycomb",
        "coralogix": "coralogix",
        "new_relic": "new relic",
        "slack": "slack",
        "discord": "discord",
        "telegram": "telegram",
        "rocketchat": "rocket.chat",
        "buzz": "buzz",
        "aws": "aws",
        "github": "github mcp",
        "sentry": "sentry",
        "gitlab": "gitlab",
        "jenkins": "jenkins",
        "google_docs": "google docs",
        "vercel": "vercel",
        "dagster": "dagster",
        "jira": "jira",
        "servicenow": "servicenow",
        "alertmanager": "alertmanager",
        "opsgenie": "opsgenie",
        "pagerduty": "pagerduty",
        "incident_io": "incident.io",
        "posthog": "posthog",
        "posthog_mcp": "posthog mcp",
        "sentry_mcp": "sentry mcp",
        "opensearch": "opensearch",
        "tempo": "grafana tempo",
    }

    step(f"Service · {_SERVICE_LABELS.get(selected_service, selected_service)}")
    if selected_service == "vercel":
        console.print(
            f"[{SECONDARY}]Note: Vercel's runtime-log API may omit or delay lines compared to the "
            "dashboard. Deployment and build checks still apply; there is no CLI incident browser.[/]"
        )
    try:
        label, env_path = handlers[selected_service]()
        if label:
            configured.append(label)
            last_env_path = env_path
    except KeyboardInterrupt:
        console.print(
            f"[{WARNING}]{_SERVICE_LABELS.get(selected_service, selected_service)} setup skipped.[/]"
        )

    return configured, last_env_path
