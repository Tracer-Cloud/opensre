"""query_openobserve_traces 工具的注册入口。"""

from __future__ import annotations

from integrations.openobserve.tools.openobserve_traces_tool.tool import (
    _map_query_openobserve_traces,
    _traces_extract_params,
    query_openobserve_traces,
)

TOOL_MODULES = ("tool",)

__all__ = [
    "TOOL_MODULES",
    "_map_query_openobserve_traces",
    "_traces_extract_params",
    "query_openobserve_traces",
]
