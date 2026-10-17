"""Registry entrypoint for the OpenSRE Analytics daily growth tool."""

from __future__ import annotations

from integrations.opensre_analytics.tools.opensre_analytics_growth_tool.tool import (
    opensre_analytics_daily_growth,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "opensre_analytics_daily_growth"]
