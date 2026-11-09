"""Registry entrypoint for query_signoz_logs."""

from __future__ import annotations

from integrations.signoz.tools.query_signoz_logs_tool.tool import (
    _logs_is_available,
    query_signoz_logs,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "_logs_is_available", "query_signoz_logs"]
