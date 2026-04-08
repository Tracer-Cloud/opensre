"""Shared constants for the OpenSRE CLI."""

from __future__ import annotations

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
    "aws",
    "coralogix",
    "datadog",
    "grafana",
    "github",
    "honeycomb",
    "mongodb",
    "mongodb_atlas",
    "opensearch",
    "rds",
    "sentry",
    "slack",
    "tracer",
    "vercel",
)

VERIFY_SERVICES: tuple[str, ...] = (
    "grafana",
    "datadog",
    "honeycomb",
    "mongodb",
    "mongodb_atlas",
    "opsgenie",
    "coralogix",
    "aws",
    "slack",
    "tracer",
    "github",
    "sentry",
    "mongodb",
    "opsgenie",
    "google_docs",
    "vercel",
    "kafka",
    "clickhouse",
    "bitbucket",
)
MANAGED_INTEGRATION_SERVICES: tuple[str, ...] = tuple(
    sorted(set(SETUP_SERVICES) | set(VERIFY_SERVICES))
)
