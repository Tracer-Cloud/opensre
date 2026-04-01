"""Datadog investigation tools."""

from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.datadog.datadog_events import DataDogEventsTool, query_datadog_events
from app.tools.tool_actions.datadog.datadog_investigate import (
    DataDogContextTool,
    fetch_datadog_context,
)
from app.tools.tool_actions.datadog.datadog_logs import DataDogLogsTool, query_datadog_logs
from app.tools.tool_actions.datadog.datadog_metrics import DataDogMetricsTool
from app.tools.tool_actions.datadog.datadog_monitors import (
    DataDogMonitorsTool,
    query_datadog_monitors,
)
from app.tools.tool_actions.datadog.datadog_node_ip_to_pods import (
    DataDogNodePodsTool,
    get_pods_on_node,
)

# Canonical tool list registered with the investigation registry.
# DataDogMetricsTool is excluded until the implementation is complete.
TOOLS: list[BaseTool] = [
    DataDogContextTool(),
    DataDogLogsTool(),
    DataDogMonitorsTool(),
    DataDogEventsTool(),
    DataDogNodePodsTool(),
]

__all__ = [
    "TOOLS",
    "DataDogContextTool",
    "DataDogEventsTool",
    "DataDogLogsTool",
    "DataDogMetricsTool",
    "DataDogMonitorsTool",
    "DataDogNodePodsTool",
    # Backward-compatible aliases
    "fetch_datadog_context",
    "get_pods_on_node",
    "query_datadog_events",
    "query_datadog_logs",
    "query_datadog_monitors",
]
