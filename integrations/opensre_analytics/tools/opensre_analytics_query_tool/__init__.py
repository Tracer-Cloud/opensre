"""Registry entrypoint for the OpenSRE Analytics query tools."""

from __future__ import annotations

from integrations.opensre_analytics.tools.opensre_analytics_query_tool.tool import (
    list_opensre_analytics_tools,
    query_opensre_analytics,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "list_opensre_analytics_tools", "query_opensre_analytics"]
