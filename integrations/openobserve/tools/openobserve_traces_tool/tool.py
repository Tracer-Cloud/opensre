"""OpenObserve 追踪（trace）检索工具。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.openobserve.tools._helpers import (
    _DEFAULT_MAX_RESULTS,
    _bounded_limit,
    _extract_records,
    _openobserve_available,
    _openobserve_extract_params,
    _post_search,
)

#: 限制回显到报告摘要中的查询文本长度。
_QUERY_SUMMARY_TRUNCATE_LEN = 80

#: 追踪数据默认的 stream 名称。
_DEFAULT_TRACE_STREAM = "traces"


def _build_trace_query(trace_id: str, query: str, stream: str) -> str:
    """构造追踪检索的 SQL 查询。

    优先使用显式 query；否则在提供 trace_id 时按 trace_id 精确检索；
    都没有时退化为按时间返回全部 span。
    """
    normalized_query = query.strip()
    if normalized_query:
        return normalized_query
    trace_name = trace_id.strip()
    stream_name = stream.strip() or _DEFAULT_TRACE_STREAM
    if trace_name:
        return (
            f"SELECT * FROM \"{stream_name}\" WHERE trace_id = '{trace_name}' "
            f"ORDER BY _timestamp DESC"
        )
    return f'SELECT * FROM "{stream_name}" ORDER BY _timestamp DESC'


def _map_query_openobserve_traces(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """引用查询返回的 span 数，以及所检索的 trace_id/查询受限片段。"""
    if not output.get("available"):
        return
    total = output.get("total_returned", 0)
    if not total:
        return
    effective_limit = output.get("effective_limit", total)
    count_label = f"{total}+" if total >= effective_limit else str(total)
    parts = [f"{count_label} span(s)"]
    trace_id = output.get("trace_id")
    if trace_id:
        safe_trace = truncate(str(trace_id).replace("\n", " "), 60)
        parts.append(f"trace_id '{safe_trace}'")
    query = truncate(
        str(output.get("query", "")).replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
        _QUERY_SUMMARY_TRUNCATE_LEN,
    )
    if query:
        parts.append(f"query '{query}'")
    record_evidence_entry(
        evidence,
        source="query_openobserve_traces",
        label="OpenObserve Traces",
        summary=", ".join(parts),
    )


def _traces_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从 sources 中提取追踪检索的入参。"""
    params = _openobserve_extract_params(sources)
    oo = sources.get("openobserve", {})
    params["trace_id"] = str(oo.get("trace_id", "")).strip()
    trace_stream = str(oo.get("trace_stream", "")).strip()
    stream = str(oo.get("stream", "")).strip()
    params["stream"] = trace_stream or stream or _DEFAULT_TRACE_STREAM
    return params


@tool(
    name="query_openobserve_traces",
    display_name="OpenObserve traces",
    source="openobserve",
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    tags=("traces", "observability"),
    description=(
        "Retrieve OpenObserve traces/spans using bounded read-only search. "
        "Pass a trace_id to inspect a single trace, or a SQL query to filter spans."
    ),
    requires=["base_url"],
    input_schema={
        "type": "object",
        "properties": {
            "base_url": {"type": "string"},
            "org": {"type": "string", "default": "default"},
            "trace_id": {"type": "string", "description": "Trace ID to look up."},
            "query": {"type": "string", "description": "Custom SQL query over the trace stream."},
            "stream": {"type": "string", "default": "traces"},
            "api_token": {"type": "string"},
            "username": {"type": "string"},
            "password": {"type": "string"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 50},
            "max_results": {"type": "integer", "default": 100},
            "integration_id": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 20.0},
        },
        "required": ["base_url"],
    },
    is_available=_openobserve_available,
    injected_params=("base_url",),
    extract_params=_traces_extract_params,
    evidence_mapper=_map_query_openobserve_traces,
)
def query_openobserve_traces(
    base_url: str,
    org: str = "default",
    trace_id: str = "",
    query: str = "",
    stream: str = "",
    api_token: str = "",
    username: str = "",
    password: str = "",
    time_range_minutes: int = 60,
    limit: int = 50,
    max_results: int = _DEFAULT_MAX_RESULTS,
    integration_id: str = "",
    timeout_seconds: float = 20.0,
    **_kwargs: Any,
) -> dict[str, Any]:
    """从 OpenObserve 获取受限的只读追踪证据。

    入参：
        base_url：OpenObserve 实例的基础 URL。
        org：组织名称，默认 "default"。
        trace_id：要检索的 trace_id；提供时构造精确匹配查询。
        query：自定义 SQL 查询；为空时基于 trace_id 或 stream 构造默认查询。
        stream：追踪数据所在的 stream 名称，默认 "traces"。
        api_token：API token，优先于 username/password 使用。
        username：用户名，用于 Basic 认证。
        password：密码，用于 Basic 认证。
        time_range_minutes：回溯的时间范围（分钟），默认 60。
        limit：请求的返回条数上限，默认 50，会被 _bounded_limit 收紧。
        max_results：配置允许的最大结果数，默认 100。
        integration_id：集成记录标识，用于错误上报。
        timeout_seconds：HTTP 请求超时秒数，默认 20.0。
    返回：
        结果字典：包含 source、available、org、integration_id、trace_id、
        query、total_returned、effective_limit 与 records；缺少必要配置
        或请求失败时返回 tool_unavailable 的结果。
    """
    base = base_url.strip().rstrip("/")
    if not base:
        return tool_unavailable("openobserve", "Missing OpenObserve URL.", records=[])
    auth_token = api_token.strip()
    user = username.strip()
    secret = password.strip()
    if not auth_token and not (user and secret):
        return tool_unavailable("openobserve", "Missing OpenObserve credentials.", records=[])

    normalized_query = _build_trace_query(trace_id, query, stream)
    effective_limit = _bounded_limit(limit, max_results)
    now = datetime.now(UTC)
    start = now - timedelta(minutes=max(1, time_range_minutes))
    payload: dict[str, Any] = {
        "query": {
            "sql": normalized_query,
            # 与日志/指标一致，采用微秒时间窗。
            "start_time": int(start.timestamp() * 1_000_000),
            "end_time": int(now.timestamp() * 1_000_000),
        },
        "size": effective_limit,
    }
    stream_name = stream.strip() or _DEFAULT_TRACE_STREAM
    payload["stream_name"] = stream_name

    try:
        body = _post_search(
            base_url=base,
            org=org,
            payload=payload,
            api_token=auth_token,
            username=user,
            password=secret,
            timeout_seconds=timeout_seconds,
            tool_name="query_openobserve_traces",
            component="integrations.openobserve.tools.openobserve_traces_tool.tool",
            integration_id=integration_id,
            type="traces",
        )
    except Exception as err:
        return tool_unavailable("openobserve", str(err), records=[])

    records = _extract_records(body)[:effective_limit]
    return {
        "source": "openobserve",
        "available": True,
        "org": (org or "default").strip() or "default",
        "integration_id": integration_id,
        "trace_id": trace_id.strip(),
        "query": normalized_query,
        "total_returned": len(records),
        "effective_limit": effective_limit,
        "records": records,
    }
