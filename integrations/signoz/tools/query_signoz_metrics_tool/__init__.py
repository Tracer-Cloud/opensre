"""Registry entrypoint for query_signoz_metrics."""

from __future__ import annotations

from integrations.signoz.tools.query_signoz_metrics_tool.tool import (
    _metrics_is_available,
    query_signoz_metrics,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "_metrics_is_available", "query_signoz_metrics"]
