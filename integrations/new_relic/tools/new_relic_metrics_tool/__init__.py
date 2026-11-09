"""Registry entrypoint for the New Relic metrics tool."""

from __future__ import annotations

from integrations.new_relic.tools.new_relic_metrics_tool.tool import (
    NewRelicMetricsTool,
    query_new_relic_metrics,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "NewRelicMetricsTool", "query_new_relic_metrics"]
