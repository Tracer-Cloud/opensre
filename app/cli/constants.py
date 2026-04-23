"""Shared constants for the OpenSRE CLI."""

from __future__ import annotations

__all__ = (
    "ALERT_TEMPLATE_CHOICES",
    "MANAGED_INTEGRATION_SERVICES",
    "SAMPLE_ALERT_OPTIONS",
    "SETUP_SERVICES",
    "VERIFY_SERVICES",
)

ALERT_TEMPLATE_CHOICES: tuple[str, ...] = (
    "generic",
    "datadog",
    "grafana",
    "honeycomb",
    "coralogix",
)

SAMPLE_ALERT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("generic", "Generic - High error rate in payments ETL"),
    ("datadog", "Datadog - payments-etl error rate high"),
    ("grafana", "Grafana - Pipeline failure rate high"),
    ("honeycomb", "Honeycomb - checkout-api latency regression"),
    ("coralogix", "Coralogix - payments worker errors"),
)

SETUP_SERVICES: tuple[str, ...] = (
    "alertmanager",
    "aws",
    "betterstack",
    "coralogix",
    "datadog",
    "discord",
    "grafana",
    "github",
    "gitlab",
    "honeycomb",
    "mariadb",
    "mongodb",
    "mongodb_atlas",
    "mysql",
    "opensearch",
    "postgresql",
    "rabbitmq",
    "rds",
    "sentry",
    "slack",
    "tracer",
    "vercel",
)

VERIFY_SERVICES: tuple[str, ...] = (
    "alertmanager",
    "aws",
    "betterstack",
    "bitbucket",
    "clickhouse",
    "coralogix",
    "datadog",
    "discord",
    "telegram",
    "github",
    "google_docs",
    "grafana",
    "honeycomb",
    "kafka",
    "mariadb",
    "mongodb",
    "mongodb_atlas",
    "mysql",
    "openclaw",
    "opsgenie",
    "postgresql",
    "rabbitmq",
    "sentry",
    "slack",
    "tracer",
    "vercel",
)
MANAGED_INTEGRATION_SERVICES: tuple[str, ...] = tuple(
    sorted(set(SETUP_SERVICES) | set(VERIFY_SERVICES))
)
