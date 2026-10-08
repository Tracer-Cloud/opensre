"""Strict models for resolved effective integrations."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from config.strict_config import StrictConfigModel


class EffectiveIntegrationEntry(StrictConfigModel):
    """Resolved integration entry with source metadata."""

    source: str
    config: dict[str, Any]
    instances: list[dict[str, Any]] | None = None


class EffectiveIntegrations(StrictConfigModel):
    """Container for normalized effective integrations.

    Fields are declared per integration so a typo in a shipped name is still
    caught. Extras are allowed on top of them, because an integration that lives
    outside this repository has no field here to fill and would otherwise be
    dropped at validation with nothing but a log line to show for it.
    """

    model_config = ConfigDict(extra="allow")

    grafana: EffectiveIntegrationEntry | None = None
    datadog: EffectiveIntegrationEntry | None = None
    groundcover: EffectiveIntegrationEntry | None = None
    honeycomb: EffectiveIntegrationEntry | None = None
    coralogix: EffectiveIntegrationEntry | None = None
    dagster: EffectiveIntegrationEntry | None = None
    aws: EffectiveIntegrationEntry | None = None
    slack: EffectiveIntegrationEntry | None = None
    tracer: EffectiveIntegrationEntry | None = None
    github: EffectiveIntegrationEntry | None = None
    sentry: EffectiveIntegrationEntry | None = None
    mongodb: EffectiveIntegrationEntry | None = None
    mongodb_atlas: EffectiveIntegrationEntry | None = None
    redis: EffectiveIntegrationEntry | None = None
    mariadb: EffectiveIntegrationEntry | None = None
    rabbitmq: EffectiveIntegrationEntry | None = None
    betterstack: EffectiveIntegrationEntry | None = None
    google_docs: EffectiveIntegrationEntry | None = None
    gitlab: EffectiveIntegrationEntry | None = None
    vercel: EffectiveIntegrationEntry | None = None
    railway: EffectiveIntegrationEntry | None = None
    jira: EffectiveIntegrationEntry | None = None
    servicenow: EffectiveIntegrationEntry | None = None
    opsgenie: EffectiveIntegrationEntry | None = None
    pagerduty: EffectiveIntegrationEntry | None = None
    incident_io: EffectiveIntegrationEntry | None = None
    prefect: EffectiveIntegrationEntry | None = None
    posthog: EffectiveIntegrationEntry | None = None
    kafka: EffectiveIntegrationEntry | None = None
    clickhouse: EffectiveIntegrationEntry | None = None
    postgresql: EffectiveIntegrationEntry | None = None
    azure_sql: EffectiveIntegrationEntry | None = None
    bitbucket: EffectiveIntegrationEntry | None = None
    trello: EffectiveIntegrationEntry | None = None
    discord: EffectiveIntegrationEntry | None = None
    telegram: EffectiveIntegrationEntry | None = None
    rocketchat: EffectiveIntegrationEntry | None = None
    buzz: EffectiveIntegrationEntry | None = None
    smtp: EffectiveIntegrationEntry | None = None
    whatsapp: EffectiveIntegrationEntry | None = None
    twilio: EffectiveIntegrationEntry | None = None
    posthog_mcp: EffectiveIntegrationEntry | None = None
    sentry_mcp: EffectiveIntegrationEntry | None = None
    x_mcp: EffectiveIntegrationEntry | None = None
    mcp_gateway: EffectiveIntegrationEntry | None = None
    mysql: EffectiveIntegrationEntry | None = None
    snowflake: EffectiveIntegrationEntry | None = None
    azure: EffectiveIntegrationEntry | None = None
    openobserve: EffectiveIntegrationEntry | None = None
    opensearch: EffectiveIntegrationEntry | None = None
    alertmanager: EffectiveIntegrationEntry | None = None
    splunk: EffectiveIntegrationEntry | None = None
    airflow: dict[str, Any] | None = None
    argocd: EffectiveIntegrationEntry | None = None
    helm: EffectiveIntegrationEntry | None = None
    victoria_logs: EffectiveIntegrationEntry | None = None
    alicloud: EffectiveIntegrationEntry | None = None
    signoz: EffectiveIntegrationEntry | None = None
    jenkins: EffectiveIntegrationEntry | None = None
    tempo: EffectiveIntegrationEntry | None = None
    temporal: EffectiveIntegrationEntry | None = None
    kubernetes: EffectiveIntegrationEntry | None = None
    new_relic: EffectiveIntegrationEntry | None = None
    yandex_cloud: EffectiveIntegrationEntry | None = None
