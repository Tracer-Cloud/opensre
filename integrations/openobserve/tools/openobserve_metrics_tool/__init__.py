"""query_openobserve_metrics 工具的注册入口。"""

from __future__ import annotations

from integrations.openobserve.tools.openobserve_metrics_tool.tool import (
    _map_query_openobserve_metrics,
    _metrics_extract_params,
    query_openobserve_metrics,
)

TOOL_MODULES = ("tool",)

__all__ = [
    "TOOL_MODULES",
    "_map_query_openobserve_metrics",
    "_metrics_extract_params",
    "query_openobserve_metrics",
]
