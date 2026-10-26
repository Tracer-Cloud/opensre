"""Registry entrypoint for query_signoz_traces."""

from __future__ import annotations

from integrations.signoz.tools.query_signoz_traces_tool.tool import (
    _traces_is_available,
    query_signoz_traces,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "_traces_is_available", "query_signoz_traces"]
