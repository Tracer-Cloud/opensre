"""OpenObserve 代理工具——每个工具一个包，在此处统一再导出。

注册通过导入时的 ``@tool`` 装饰器完成。注册表直接发现每个工具包
（``tools.registry_discovery`` 遍历本包并遵循各包的 ``TOOL_MODULES``
清单），因此这些再导出是为从 ``integrations.openobserve.tools`` 导入的
调用方与测试而存在，而非用于注册。
"""

from __future__ import annotations

from integrations.openobserve.tools.openobserve_logs_tool import (
    _map_query_openobserve_logs,
    _openobserve_extract_params,
    query_openobserve_logs,
)
from integrations.openobserve.tools.openobserve_metrics_tool import (
    _map_query_openobserve_metrics,
    _metrics_extract_params,
    query_openobserve_metrics,
)
from integrations.openobserve.tools.openobserve_streams_tool import (
    _map_list_openobserve_streams,
    _streams_extract_params,
    list_openobserve_streams,
)
from integrations.openobserve.tools.openobserve_traces_tool import (
    _map_query_openobserve_traces,
    _traces_extract_params,
    query_openobserve_traces,
)

__all__ = [
    "_map_list_openobserve_streams",
    "_map_query_openobserve_logs",
    "_map_query_openobserve_metrics",
    "_map_query_openobserve_traces",
    "_metrics_extract_params",
    "_openobserve_extract_params",
    "_streams_extract_params",
    "_traces_extract_params",
    "list_openobserve_streams",
    "query_openobserve_logs",
    "query_openobserve_metrics",
    "query_openobserve_traces",
]
