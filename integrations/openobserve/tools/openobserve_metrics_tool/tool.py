"""OpenObserve 指标（PromQL）查询工具。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from config.constants.openobserve import OPENOBSERVE_MAX_RESULTS_DEFAULT
from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.openobserve.tools._helpers import (
    _bounded_limit,
    _get_json,
    _openobserve_available,
    _openobserve_extract_params,
)

#: 限制回显到报告摘要中的 PromQL 查询文本长度。
_QUERY_SUMMARY_TRUNCATE_LEN = 80


def _extract_metric_results(body: dict[str, Any]) -> list[dict[str, Any]]:
    """从 Prometheus 兼容的 ``data.result`` 响应中提取指标序列。"""
    data = body.get("data")
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
    return []


def _map_query_openobserve_metrics(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """引用查询返回的指标序列数，以及所执行的 PromQL 受限片段。"""
    if not output.get("available"):
        return
    total = output.get("total_returned", 0)
    if not total:
        return
    effective_limit = output.get("effective_limit", total)
    count_label = f"{total}+" if total >= effective_limit else str(total)
    parts = [f"{count_label} metric series"]
    promql = truncate(
        str(output.get("promql", "")).replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
        _QUERY_SUMMARY_TRUNCATE_LEN,
    )
    if promql:
        parts.append(f"promql '{promql}'")
    record_evidence_entry(
        evidence,
        source="query_openobserve_metrics",
        label="OpenObserve Metrics",
        summary=", ".join(parts),
    )


def _metrics_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从 sources 中提取指标查询的入参。"""
    params = _openobserve_extract_params(sources)
    oo = sources.get("openobserve", {})
    params["promql"] = str(oo.get("promql", "")).strip()
    params["step_seconds"] = int(oo.get("step_seconds", 0) or 0)
    return params


@tool(
    name="query_openobserve_metrics",
    display_name="OpenObserve metrics",
    source="openobserve",
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    tags=("metrics", "observability"),
    description=(
        "Query OpenObserve metrics using PromQL with bounded read-only search. "
        "Run PromQL expressions such as rate(http_requests_total[5m]) or up."
    ),
    requires=["base_url"],
    input_schema={
        "type": "object",
        "properties": {
            "base_url": {"type": "string"},
            "org": {"type": "string", "default": "default"},
            "promql": {
                "type": "string",
                "description": "PromQL expression to evaluate, e.g. rate(http_requests_total[5m]).",
            },
            "api_token": {"type": "string"},
            "username": {"type": "string"},
            "password": {"type": "string"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "step_seconds": {
                "type": "integer",
                "default": 0,
                "description": "Optional resolution step in seconds for range queries.",
            },
            "limit": {"type": "integer", "default": 50},
            "max_results": {"type": "integer", "default": 100},
            "integration_id": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 20.0},
        },
        "required": ["base_url", "promql"],
    },
    is_available=_openobserve_available,
    injected_params=("base_url",),
    extract_params=_metrics_extract_params,
    evidence_mapper=_map_query_openobserve_metrics,
)
def query_openobserve_metrics(
    base_url: str,
    org: str = "default",
    promql: str = "",
    api_token: str = "",
    username: str = "",
    password: str = "",
    time_range_minutes: int = 60,
    step_seconds: int = 0,
    limit: int = 50,
    max_results: int = OPENOBSERVE_MAX_RESULTS_DEFAULT,
    integration_id: str = "",
    timeout_seconds: float = 20.0,
    **_kwargs: Any,
) -> dict[str, Any]:
    """从 OpenObserve 获取受限的只读指标证据。

    入参：
        base_url：OpenObserve 实例的基础 URL。
        org：组织名称，默认 "default"。
        promql：要执行的 PromQL 表达式。
        api_token：API token，优先于 username/password 使用。
        username：用户名，用于 Basic 认证。
        password：密码，用于 Basic 认证。
        time_range_minutes：回溯的时间范围（分钟），默认 60。
        step_seconds：范围查询的解析步长（秒），0 表示不指定。
        limit：请求的返回条数上限，默认 50，会被 _bounded_limit 收紧。
        max_results：配置允许的最大结果数，默认 100。
        integration_id：集成记录标识，用于错误上报。
        timeout_seconds：HTTP 请求超时秒数，默认 20.0。
    返回：
        结果字典：包含 source、available、org、integration_id、promql、
        total_returned、effective_limit 与 records；缺少必要配置或请求
        失败时返回 tool_unavailable 的结果。
    """
    base = base_url.strip().rstrip("/")
    if not base:
        return tool_unavailable("openobserve", "Missing OpenObserve URL.", records=[])
    auth_token = api_token.strip()
    user = username.strip()
    secret = password.strip()
    if not auth_token and not (user and secret):
        return tool_unavailable("openobserve", "Missing OpenObserve credentials.", records=[])

    normalized_promql = promql.strip()
    if not normalized_promql:
        return tool_unavailable(
            "openobserve",
            "No PromQL query given - pass a PromQL expression such as "
            "'rate(http_requests_total[5m])'.",
            records=[],
        )

    effective_limit = _bounded_limit(limit, max_results)
    now = datetime.now(UTC)
    start = now - timedelta(minutes=max(1, time_range_minutes))
    org_name = (org or "default").strip() or "default"
    endpoint = f"{base}/api/{org_name}/prometheus/api/v1/query_range"
    query_params = {
        "query": normalized_promql,
        "start": str(start.timestamp()),
        "end": str(now.timestamp()),
    }
    if step_seconds and int(step_seconds) > 0:
        query_params["step"] = str(int(step_seconds))

    try:
        body = _get_json(
            url=endpoint,
            params=query_params,
            api_token=auth_token,
            username=user,
            password=secret,
            timeout_seconds=timeout_seconds,
            tool_name="query_openobserve_metrics",
            component="integrations.openobserve.tools.openobserve_metrics_tool.tool",
            integration_id=integration_id,
        )
    except Exception as err:
        return tool_unavailable("openobserve", str(err), records=[])

    records = _extract_metric_results(body if isinstance(body, dict) else {})[:effective_limit]
    return {
        "source": "openobserve",
        "available": True,
        "org": (org or "default").strip() or "default",
        "integration_id": integration_id,
        "promql": normalized_promql,
        "total_returned": len(records),
        "effective_limit": effective_limit,
        "records": records,
    }
