"""Evidence source type — the canonical set of data source identifiers."""

from __future__ import annotations

from typing import Literal

EvidenceSource = Literal[
    "storage",
    "batch",
    "tracer_web",
    "cloudwatch",
    "aws_sdk",
    "knowledge",
    "grafana",
    "datadog",
    "honeycomb",
    "coralogix",
    "eks",
    "github",
    "sentry",
    "mongodb",
    "kafka",
    "clickhouse",
    "google_docs",
    "vercel",
    "opsgenie",
    "elasticsearch",
    "prefect",
    "gitlab",
    "bitbucket",
]
