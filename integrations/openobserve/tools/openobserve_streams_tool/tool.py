"""OpenObserve 流（streams）发现工具。"""

from __future__ import annotations

from typing import Any

from config.constants.openobserve import OPENOBSERVE_MAX_RESULTS_DEFAULT
from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.openobserve.tools._helpers import (
    _bounded_limit,
    _get_json,
    _openobserve_available,
    _openobserve_extract_params,
)


def _extract_streams(body: Any) -> list[dict[str, Any]]:
    """从 streams API 响应中提取流列表。

    兼容两种响应形状：``{"streams": [...]}`` 与裸数组；其中的元素既
    可能是字典（含 name/type 等字段），也可能是旧版返回的字符串流名。

    入参：
        body：streams API 的响应体。
    返回：
        流字典列表，每个元素至少含 ``name`` 键；无法解析时返回空列表。
    """
    if isinstance(body, list):
        result: list[dict[str, Any]] = []
        for item in body:
            if isinstance(item, dict):
                result.append(item)
            elif isinstance(item, str):
                result.append({"name": item})
        return result
    if not isinstance(body, dict):
        return []
    streams = body.get("streams")
    if not isinstance(streams, list):
        return []
    result = []
    for item in streams:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str):
            result.append({"name": item})
    return result


def _map_list_openobserve_streams(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """引用查询返回的流数量。"""
    if not output.get("available"):
        return
    total = output.get("total_returned", 0)
    if not total:
        return
    effective_limit = output.get("effective_limit", total)
    count_label = f"{total}+" if total >= effective_limit else str(total)
    record_evidence_entry(
        evidence,
        source="list_openobserve_streams",
        label="OpenObserve Streams",
        summary=f"{count_label} stream(s) found",
    )


def _streams_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从 sources 中提取流发现工具的入参。"""
    return _openobserve_extract_params(sources)


@tool(
    name="list_openobserve_streams",
    display_name="OpenObserve streams",
    source="openobserve",
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    tags=("streams", "observability"),
    description=(
        "List OpenObserve streams (logs, metrics, traces) using bounded read-only search. "
        "Useful for discovering which streams exist before querying logs or traces."
    ),
    requires=["base_url"],
    input_schema={
        "type": "object",
        "properties": {
            "base_url": {"type": "string"},
            "org": {"type": "string", "default": "default"},
            "api_token": {"type": "string"},
            "username": {"type": "string"},
            "password": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
            "max_results": {"type": "integer", "default": 100},
            "integration_id": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 20.0},
        },
        "required": ["base_url"],
    },
    is_available=_openobserve_available,
    injected_params=("base_url",),
    extract_params=_streams_extract_params,
    evidence_mapper=_map_list_openobserve_streams,
)
def list_openobserve_streams(
    base_url: str,
    org: str = "default",
    api_token: str = "",
    username: str = "",
    password: str = "",
    limit: int = 50,
    max_results: int = OPENOBSERVE_MAX_RESULTS_DEFAULT,
    integration_id: str = "",
    timeout_seconds: float = 20.0,
    **_kwargs: Any,
) -> dict[str, Any]:
    """列出 OpenObserve 组织下的数据流（受限只读）。

    入参：
        base_url：OpenObserve 实例的基础 URL。
        org：组织名称，默认 "default"。
        api_token：API token，优先于 username/password 使用。
        username：用户名，用于 Basic 认证。
        password：密码，用于 Basic 认证。
        limit：请求的返回条数上限，默认 50，会被 _bounded_limit 收紧。
        max_results：配置允许的最大结果数，默认 100。
        integration_id：集成记录标识，用于错误上报。
        timeout_seconds：HTTP 请求超时秒数，默认 20.0。
    返回：
        结果字典：包含 source、available、org、integration_id、
        total_returned、effective_limit 与 streams；缺少必要配置或请求
        失败时返回 tool_unavailable 的结果。
    """
    base = base_url.strip().rstrip("/")
    if not base:
        return tool_unavailable("openobserve", "Missing OpenObserve URL.", streams=[])
    auth_token = api_token.strip()
    user = username.strip()
    secret = password.strip()
    if not auth_token and not (user and secret):
        return tool_unavailable("openobserve", "Missing OpenObserve credentials.", streams=[])

    effective_limit = _bounded_limit(limit, max_results)
    org_name = (org or "default").strip() or "default"
    endpoint = f"{base}/api/{org_name}/streams"
    try:
        body = _get_json(
            url=endpoint,
            api_token=auth_token,
            username=user,
            password=secret,
            timeout_seconds=timeout_seconds,
            tool_name="list_openobserve_streams",
            component="integrations.openobserve.tools.openobserve_streams_tool.tool",
            integration_id=integration_id,
        )
    except Exception as err:
        return tool_unavailable("openobserve", str(err), streams=[])

    streams = _extract_streams(body)[:effective_limit]
    return {
        "source": "openobserve",
        "available": True,
        "org": org_name,
        "integration_id": integration_id,
        "total_returned": len(streams),
        "effective_limit": effective_limit,
        "streams": streams,
    }
