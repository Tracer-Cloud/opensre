"""Grafana investigation tools."""

from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.grafana.grafana_actions import (
    GrafanaAlertRulesTool,
    GrafanaLogsTool,
    GrafanaMetricsTool,
    GrafanaServiceNamesTool,
    GrafanaTracesTool,
    query_grafana_alert_rules,
    query_grafana_alert_rules_tool,
    query_grafana_logs,
    query_grafana_logs_tool,
    query_grafana_metrics,
    query_grafana_metrics_tool,
    query_grafana_service_names,
    query_grafana_service_names_tool,
    query_grafana_traces,
    query_grafana_traces_tool,
)

# Canonical tool list registered with the investigation registry.
TOOLS: list[BaseTool] = [
    GrafanaLogsTool(),
    GrafanaTracesTool(),
    GrafanaMetricsTool(),
    GrafanaAlertRulesTool(),
    GrafanaServiceNamesTool(),
]

__all__ = [
    "TOOLS",
    "GrafanaAlertRulesTool",
    "GrafanaLogsTool",
    "GrafanaMetricsTool",
    "GrafanaServiceNamesTool",
    "GrafanaTracesTool",
    # Backward-compatible aliases
    "query_grafana_alert_rules",
    "query_grafana_alert_rules_tool",
    "query_grafana_logs",
    "query_grafana_logs_tool",
    "query_grafana_metrics",
    "query_grafana_metrics_tool",
    "query_grafana_service_names",
    "query_grafana_service_names_tool",
    "query_grafana_traces",
    "query_grafana_traces_tool",
]
