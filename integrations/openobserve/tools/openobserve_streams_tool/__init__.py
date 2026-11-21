"""list_openobserve_streams 工具的注册入口。"""

from __future__ import annotations

from integrations.openobserve.tools.openobserve_streams_tool.tool import (
    _map_list_openobserve_streams,
    _streams_extract_params,
    list_openobserve_streams,
)

TOOL_MODULES = ("tool",)

__all__ = [
    "TOOL_MODULES",
    "_map_list_openobserve_streams",
    "_streams_extract_params",
    "list_openobserve_streams",
]
