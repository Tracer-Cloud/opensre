"""Registry entrypoint for the Dynatrace logs tool."""

from __future__ import annotations

from integrations.dynatrace.tools.query_dynatrace_logs_tool.tool import (
    DynatraceLogsTool,
    query_dynatrace_logs,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "DynatraceLogsTool", "query_dynatrace_logs"]
