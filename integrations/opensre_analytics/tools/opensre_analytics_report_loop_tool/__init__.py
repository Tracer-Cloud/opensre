"""Registry entrypoint for scheduling the daily growth report."""

from __future__ import annotations

from integrations.opensre_analytics.tools.opensre_analytics_report_loop_tool.tool import (
    schedule_analytics_growth_report,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "schedule_analytics_growth_report"]
