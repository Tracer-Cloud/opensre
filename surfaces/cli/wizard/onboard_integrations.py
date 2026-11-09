"""Onboard wizard integration picker taxonomy and choices."""

from __future__ import annotations

from surfaces.cli.wizard.components import Choice

ONBOARD_INTEGRATION_GROUP_ORDER: tuple[str, ...] = (
    "Observability",
    "Infrastructure & CI",
    "Incident & Comms",
    "Dev & Deploy",
    "MCP & Protocols",
)

ONBOARD_INTEGRATION_CHOICES: tuple[Choice, ...] = (
    Choice(
        value="grafana_local",
        label="Grafana Local (Docker)",
        group="Observability",
        hint="Starts Grafana + Loki and seeds demo alerts",
    ),
    Choice(
        value="grafana",
        label="Grafana Cloud / self-hosted",
        group="Observability",
        hint="Connect an existing Grafana instance",
    ),
    Choice(
        value="datadog",
        label="Datadog",
        group="Observability",
        hint="Logs, monitors, and Kubernetes context",
    ),
    Choice(
        value="honeycomb",
        label="Honeycomb",
        group="Observability",
        hint="Query traces and spans from Honeycomb",
    ),
    Choice(
        value="coralogix",
        label="Coralogix",
        group="Observability",
        hint="Query logs from Coralogix DataPrime",
    ),
    Choice(
        value="new_relic",
        label="New Relic",
        group="Observability",
        hint="Query alerts and NRQL metrics",
    ),
    Choice(
        value="sentry",
        label="Sentry",
        group="Observability",
        hint="Investigate errors, events, and issue history",
    ),
    Choice(
        value="posthog",
        label="PostHog",
        group="Observability",
        hint="Store PostHog REST credentials for your project",
    ),
    Choice(
        value="betterstack",
        label="Better Stack Telemetry",
        group="Observability",
        hint="Query logs from Better Stack (ClickHouse SQL over HTTP)",
    ),
    Choice(
        value="splunk",
        label="Splunk",
        group="Observability",
        hint="Query logs from Splunk",
    ),
    Choice(
        value="opensearch",
        label="OpenSearch / Elasticsearch",
        group="Observability",
        hint="Query logs and indices from OpenSearch or Elasticsearch clusters",
    ),
    Choice(
        value="tempo",
        label="Grafana Tempo",
        group="Observability",
        hint="Query distributed traces from a standalone Tempo backend",
    ),
    Choice(
        value="aws",
        label="AWS",
        group="Infrastructure & CI",
        hint="Inspect CloudWatch, EKS, and account resources",
    ),
    Choice(
        value="jenkins",
        label="Jenkins",
        group="Infrastructure & CI",
        hint="Correlate failed builds and deployments with incidents",
    ),
    Choice(
        value="dagster",
        label="Dagster",
        group="Infrastructure & CI",
        hint="Pipeline runs, asset materializations, and tick history",
    ),
    Choice(
        value="jira",
        label="Jira",
        group="Incident & Comms",
        hint="File and update incident tickets automatically",
    ),
    Choice(
        value="servicenow",
        label="ServiceNow",
        group="Incident & Comms",
        hint="Connect a ServiceNow instance and verify its credentials",
    ),
    Choice(
        value="alertmanager",
        label="Alertmanager",
        group="Incident & Comms",
        hint="Query firing alerts and silences from Prometheus Alertmanager",
    ),
    Choice(
        value="pagerduty",
        label="PagerDuty",
        group="Incident & Comms",
        hint="Fetch incidents, on-call schedules, and service topology from PagerDuty",
    ),
    Choice(
        value="incident_io",
        label="incident.io",
        group="Incident & Comms",
        hint="Read incident context and updates from incident.io",
    ),
    Choice(
        value="slack",
        label="Slack",
        group="Incident & Comms",
        hint="Send findings to a webhook or channel",
    ),
    Choice(
        value="discord",
        label="Discord",
        group="Incident & Comms",
        hint="Chat with the agent from Slack threads",
    ),
    Choice(
        value="telegram",
        label="Telegram",
        group="Incident & Comms",
        hint="Post findings to a Telegram chat",
    ),
    Choice(
        value="rocketchat",
        label="Rocket.Chat",
        group="Incident & Comms",
        hint="Post findings to a Rocket.Chat channel",
    ),
    Choice(
        value="buzz",
        label="Buzz",
        group="Incident & Comms",
        hint="Post findings to a Buzz (Nostr) channel — requires the buzz CLI",
    ),
    Choice(
        value="google_docs",
        label="Google Docs",
        group="Incident & Comms",
        hint="Create shareable incident postmortem reports",
    ),
    Choice(
        value="gitlab",
        label="Gitlab",
        group="Dev & Deploy",
        hint="Let the agent inspect repos, PRs, and issues",
    ),
    Choice(
        value="vercel",
        label="Vercel",
        group="Dev & Deploy",
        hint=("Deployments, build output, and logs tools; runtime-log API can lag the dashboard"),
    ),
    Choice(
        value="github",
        label="GitHub MCP",
        group="MCP & Protocols",
        hint="Let the agent inspect repos, PRs, and issues",
    ),
    Choice(
        value="posthog_mcp",
        label="PostHog (MCP)",
        group="MCP & Protocols",
        hint="Query PostHog analytics, feature flags, error tracking, and HogQL via MCP",
    ),
    Choice(
        value="sentry_mcp",
        label="Sentry (MCP)",
        group="MCP & Protocols",
        hint="Query Sentry issues, events, traces, and Seer root-cause analysis via MCP",
    ),
)

ONBOARD_SKIP_CHOICE = Choice(
    value="skip",
    label="Skip for now",
    hint="Finish setup without configuring an integration",
)
