"""Grafana owns PromQL draft fences for unformed metric answers.

Core's metric floor decides *when* to append a draft; this package supplies
the Grafana dialect.
"""

from __future__ import annotations

from infrastructure.harness_providers import (
    register_metric_query_draft,
    register_metric_query_tools,
)

# Grafana tools that return a series / log-derived metric.
_METRIC_QUERY_TOOLS = ("query_grafana_metrics", "query_grafana_logs")

_DRAFT_PROMQL = """```promql
-- Draft PromQL: confirm metric name and window, then run in Grafana.
-- This is not a live reading.
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1h])) by (le))
```"""


def register_grafana_metric_drafts() -> None:
    """Opt Grafana into the unformed-metric draft floor and query classification."""
    register_metric_query_draft("grafana", count_draft=_DRAFT_PROMQL, priority=20)
    register_metric_query_tools("grafana", _METRIC_QUERY_TOOLS)


__all__ = ["register_grafana_metric_drafts"]
