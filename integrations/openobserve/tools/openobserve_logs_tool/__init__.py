"""OpenObserve log 搜索工具——受限的只读检索。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.openobserve.tools._helpers import (
    _DEFAULT_MAX_RESULTS,
    _bounded_limit,
    _extract_records,
    _openobserve_available,
    _post_search,
)

#: 限制回显到报告摘要中的 SQL 查询文本长度——它可能是调用方
#: 构造的长查询或多行查询，而不只是默认形式。
_QUERY_SUMMARY_TRUNCATE_LEN = 80


def _map_query_openobserve_logs(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """引用查询返回的行数、stream，以及所执行查询的受限片段。

    查询运行后 ``records`` 会被截断至 ``effective_limit``，因此当返回
    的计数恰好等于该上限时，实际命中的行数可能更多——此时应采用
    "N+" 计数约定。
    """
    if not output.get("available"):
        return
    total = output.get("total_returned", 0)
    if not total:
        return
    effective_limit = output.get("effective_limit", total)
    count_label = f"{total}+" if total >= effective_limit else str(total)
    parts = [f"{count_label} record(s)"]
    stream = output.get("stream")
    if stream:
        safe_stream = truncate(str(stream).replace("\n", " "), 60)
        parts.append(f"stream '{safe_stream}'")
    query = truncate(
        str(output.get("query", "")).replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
        _QUERY_SUMMARY_TRUNCATE_LEN,
    )
    if query:
        parts.append(f"query '{query}'")
    record_evidence_entry(
        evidence,
        source="query_openobserve_logs",
        label="OpenObserve Logs",
        summary=", ".join(parts),
    )


def _openobserve_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从 sources 中提取 OpenObserve 工具的入参。

    入参：
        sources：各集成的凭据与状态字典，必须包含 "openobserve" 键。
    返回：
        参数字典：包含 base_url、org、stream、query、api_token、
        username、password、time_range_minutes、limit、max_results、
        integration_id 等字段。
    """
    oo = sources["openobserve"]
    return {
        "base_url": str(oo.get("base_url", "")).strip(),
        "org": str(oo.get("org", "default")).strip() or "default",
        "stream": str(oo.get("stream", "")).strip(),
        "query": str(oo.get("query", "")).strip(),
        "api_token": str(oo.get("api_token", "")).strip(),
        "username": str(oo.get("username", "")).strip(),
        "password": str(oo.get("password", "")).strip(),
        "time_range_minutes": int(oo.get("time_range_minutes", 60) or 60),
        "limit": 50,
        "max_results": int(oo.get("max_results", _DEFAULT_MAX_RESULTS) or _DEFAULT_MAX_RESULTS),
        "integration_id": str(oo.get("integration_id", "")).strip(),
    }


@tool(
    name="query_openobserve_logs",
    description="Query OpenObserve logs using bounded read-only search.",
    source="openobserve",
    surfaces=(ToolSurface.CHAT,),
    requires=["base_url"],
    input_schema={
        "type": "object",
        "properties": {
            "base_url": {"type": "string"},
            "org": {"type": "string", "default": "default"},
            "stream": {"type": "string", "default": ""},
            "query": {"type": "string"},
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
    extract_params=_openobserve_extract_params,
    evidence_mapper=_map_query_openobserve_logs,
)
def query_openobserve_logs(
    base_url: str,
    org: str = "default",
    stream: str = "",
    query: str = "",
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
    """从 OpenObserve 获取受限的只读证据。

    入参：
        base_url：OpenObserve 实例的基础 URL。
        org：组织名称，默认 "default"。
        stream：目标 stream 名称；未显式提供 query 时用于构造
            默认的 error 级别查询。
        query：要执行的 SQL 查询；为空时基于 stream 构造默认查询。
        api_token：API token，优先于 username/password 使用。
        username：用户名，用于 Basic 认证。
        password：密码，用于 Basic 认证。
        time_range_minutes：回溯的时间范围（分钟），默认 60。
        limit：请求的返回条数上限，默认 50，会被 _bounded_limit 收紧。
        max_results：配置允许的最大结果数，默认 100。
        integration_id：集成记录标识，用于错误上报。
        timeout_seconds：HTTP 请求超时秒数，默认 20.0。
    返回：
        结果字典：包含 source、available、org、stream、integration_id、
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

    effective_limit = _bounded_limit(limit, max_results)
    now = datetime.now(UTC)
    start = now - timedelta(minutes=max(1, time_range_minutes))
    stream_name = stream.strip()
    normalized_query = query.strip()
    if not normalized_query:
        if not stream_name:
            return tool_unavailable(
                "openobserve",
                "No query given and no stream configured to build a default one "
                "against - set OPENOBSERVE_STREAM or pass an explicit query.",
                records=[],
            )
        normalized_query = (
            f"SELECT * FROM \"{stream_name}\" WHERE level = 'error' ORDER BY _timestamp DESC"
        )

    payload: dict[str, Any] = {
        "query": {
            "sql": normalized_query,
            # OpenObserve 的 _search API 采用自 epoch 起的微秒作为
            # start_time/end_time，而非毫秒——毫秒值会落到
            # Unix epoch 附近的时间范围，从而静默地匹配不到任何内容
            # （不报错），而不是抛出异常。
            "start_time": int(start.timestamp() * 1_000_000),
            "end_time": int(now.timestamp() * 1_000_000),
        },
        "size": effective_limit,
    }
    if stream_name:
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
            tool_name="query_openobserve_logs",
            component="integrations.openobserve.tools.openobserve_logs_tool",
            integration_id=integration_id,
        )
    except Exception as err:
        return tool_unavailable("openobserve", str(err), records=[])

    records = _extract_records(body if isinstance(body, dict) else {})[:effective_limit]
    return {
        "source": "openobserve",
        "available": True,
        "org": (org or "default").strip() or "default",
        "stream": stream.strip(),
        "integration_id": integration_id,
        "query": normalized_query,
        "total_returned": len(records),
        "effective_limit": effective_limit,
        "records": records,
    }
