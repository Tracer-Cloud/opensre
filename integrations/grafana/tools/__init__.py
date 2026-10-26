"""Grafana agent tools — one package per tool, re-exported here.

Registration happens by ``@tool`` decorator at import time. The registry
discovers each tool package directly (``tools.registry_discovery`` walks this
package), so these re-exports exist for callers and tests that import from
``integrations.grafana.tools``, not to register anything.
"""

from __future__ import annotations

from integrations.grafana.client import get_grafana_client_from_credentials
from integrations.grafana.tools._helpers import (
    GRAFANA_RUNTIME_PARAMS,
    _grafana_available,
    _grafana_creds,
    _grafana_source,
    _resolve_grafana_client,
)
from integrations.grafana.tools.grafana_alert_rules_tool import query_grafana_alert_rules
from integrations.grafana.tools.grafana_annotations_tool import (
    _iso_to_epoch_ms,
    query_grafana_annotations,
)
from integrations.grafana.tools.grafana_logs_tool import query_grafana_logs
from integrations.grafana.tools.grafana_metrics_tool import (
    QueryGrafanaMetricsInput,
    QueryGrafanaMetricsOutput,
    query_grafana_metrics,
)
from integrations.grafana.tools.grafana_service_names_tool import query_grafana_service_names
from integrations.grafana.tools.grafana_traces_tool import query_grafana_traces

# Alias kept for older call sites / tests that used the private constant name.
_GRAFANA_RUNTIME_PARAMS = GRAFANA_RUNTIME_PARAMS

__all__ = [
    "GRAFANA_RUNTIME_PARAMS",
    "QueryGrafanaMetricsInput",
    "QueryGrafanaMetricsOutput",
    "_GRAFANA_RUNTIME_PARAMS",
    "_grafana_available",
    "_grafana_creds",
    "_grafana_source",
    "_iso_to_epoch_ms",
    "_resolve_grafana_client",
    "get_grafana_client_from_credentials",
    "query_grafana_alert_rules",
    "query_grafana_annotations",
    "query_grafana_logs",
    "query_grafana_metrics",
    "query_grafana_service_names",
    "query_grafana_traces",
]
