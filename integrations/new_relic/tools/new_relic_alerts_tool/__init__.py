"""Registry entrypoint for the New Relic alerts tool."""

from __future__ import annotations

from integrations.new_relic.tools.new_relic_alerts_tool.tool import (
    NewRelicAlertsTool,
    query_new_relic_alerts,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "NewRelicAlertsTool", "query_new_relic_alerts"]
