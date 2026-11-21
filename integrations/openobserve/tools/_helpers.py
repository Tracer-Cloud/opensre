"""OpenObserve 工具共享的只读辅助函数。"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from config.constants.openobserve import (
    OPENOBSERVE_MAX_RESULTS_DEFAULT,
    OPENOBSERVE_MAX_RESULTS_HARD_LIMIT,
)
from core.tool import report_run_error


def _bounded_limit(limit: int, max_results: int) -> int:
    """将请求的 limit 收敛到安全区间内。

    入参：
        limit：调用方请求的返回条数上限。
        max_results：配置允许的最大结果数。
    返回：
        收紧后的实际生效条数：不小于 1，且同时不超过 max_results
        与 OPENOBSERVE_MAX_RESULTS_HARD_LIMIT 中的较小值。
    """
    safe_max = max(1, min(max_results, OPENOBSERVE_MAX_RESULTS_HARD_LIMIT))
    return max(1, min(limit, safe_max))


def _openobserve_available(sources: dict[str, dict[str, Any]]) -> bool:
    """判断 OpenObserve 工具在当前 source 配置下是否可用。

    入参：
        sources：各集成的凭据与状态字典，其中 "openobserve" 键对应
            OpenObserve 的配置。
    返回：
        布尔值：连接已验证、存在 base_url，且提供了 api_token 或
        （同时提供了 username 与 password）时为 True，否则为 False。
    """
    oo = sources.get("openobserve", {})
    has_token = bool(str(oo.get("api_token", "")).strip())
    has_user_password = bool(
        str(oo.get("username", "")).strip() and str(oo.get("password", "")).strip()
    )
    return bool(
        oo.get("connection_verified") and oo.get("base_url") and (has_token or has_user_password)
    )


def _openobserve_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从 sources 中提取 OpenObserve 工具共用的基础入参。

    入参：
        sources：各集成的凭据与状态字典，其中 "openobserve" 键对应
            OpenObserve 的配置。
    返回：
        参数字典：包含 base_url、org、stream、query、api_token、
        username、password、time_range_minutes、limit、max_results、
        integration_id 等字段；调用方可在此基础上补充工具专属字段。
    """
    oo = sources.get("openobserve", {})
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
        "max_results": int(
            oo.get("max_results", OPENOBSERVE_MAX_RESULTS_DEFAULT)
            or OPENOBSERVE_MAX_RESULTS_DEFAULT
        ),
        "integration_id": str(oo.get("integration_id", "")).strip(),
    }


def _auth_headers(api_token: str, username: str, password: str) -> dict[str, str]:
    """构造 OpenObserve 请求所需的认证头。

    入参：
        api_token：API token；非空时优先使用 Bearer 认证。
        username：用户名，与 password 配合使用 Basic 认证。
        password：密码。
    返回：
        认证头字典：优先返回 Bearer token 头；未提供 token 时返回
        基于 username 与 password 的 Basic Auth 头。
    """
    if api_token:
        return {"Authorization": f"Bearer {api_token}"}
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {credentials}"}


def _extract_records(body: dict[str, Any]) -> list[dict[str, Any]]:
    """从 OpenObserve 搜索响应中提取记录列表。

    兼容多种响应结构：先尝试嵌套的 hits.hits（取每个 hit 的 _source），
    再依次尝试平铺的 hits 列表、records 字段与 data 字段；均不可用
    时返回空列表。

    入参：
        body：OpenObserve _search API 的响应体字典。
    返回：
        记录字典列表，其中元素均为 dict；无法解析时返回空列表。
    """
    hits = body.get("hits")
    if isinstance(hits, dict):
        hit_docs = hits.get("hits", [])
        if isinstance(hit_docs, list):
            records: list[dict[str, Any]] = []
            for hit in hit_docs:
                if isinstance(hit, dict):
                    source = hit.get("_source", {})
                    if isinstance(source, dict):
                        records.append(source)
            return records
    if isinstance(hits, list):
        return [item for item in hits if isinstance(item, dict)]
    raw_records = body.get("records")
    if isinstance(raw_records, list):
        return [item for item in raw_records if isinstance(item, dict)]
    data = body.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _post_search(
    *,
    base_url: str,
    org: str,
    payload: dict[str, Any],
    api_token: str,
    username: str,
    password: str,
    timeout_seconds: float,
    tool_name: str,
    component: str,
    integration_id: str = "",
    type: str = "",
) -> dict[str, Any]:
    """向 OpenObserve ``_search`` 端点发送只读搜索请求。

    入参：
        base_url：OpenObserve 实例的基础 URL。
        org：组织名称，用于构造端点路径。
        payload：搜索请求体（含 SQL 与微秒时间窗）。
        api_token：API token，优先于 username/password 使用。
        username：用户名，用于 Basic 认证。
        password：密码，用于 Basic 认证。
        timeout_seconds：HTTP 请求超时秒数。
        tool_name：当前工具名称，用于错误上报。
        component：当前工具所在模块路径，用于错误上报。
        integration_id：集成记录标识，用于错误上报。
        type：可选的查询类型参数（如 "traces"），追加为 ``?type=...``。
    返回：
        解析后的 JSON 响应体字典；无法解析时返回空字典。
    抛出：
        网络错误或非 2xx 响应错误会在记录运行错误后重新抛出，
        由调用方转换为 tool_unavailable 结果。
    """
    org_name = (org or "default").strip() or "default"
    endpoint = f"{base_url}/api/{org_name}/_search"
    if type:
        endpoint = f"{endpoint}?type={type}"
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_headers(api_token, username, password))
    try:
        response = httpx.post(
            endpoint, headers=headers, json=payload, timeout=max(1.0, timeout_seconds)
        )
        response.raise_for_status()
        body = response.json()
    except Exception as err:
        report_run_error(
            err,
            tool_name=tool_name,
            source="openobserve",
            component=component,
            method="httpx.post",
            extras={"endpoint": endpoint, "integration_id": integration_id},
        )
        raise
    return body if isinstance(body, dict) else {}


def _get_json(
    *,
    url: str,
    params: dict[str, str] | None = None,
    api_token: str,
    username: str,
    password: str,
    timeout_seconds: float,
    tool_name: str,
    component: str,
    integration_id: str = "",
) -> Any:
    """向 OpenObserve 发起带认证的只读 GET 请求。

    入参：
        url：完整的目标 URL（含端点路径）。
        params：可选的 URL 查询参数。
        api_token：API token，优先于 username/password 使用。
        username：用户名，用于 Basic 认证。
        password：密码，用于 Basic 认证。
        timeout_seconds：HTTP 请求超时秒数。
        tool_name：当前工具名称，用于错误上报。
        component：当前工具所在模块路径，用于错误上报。
        integration_id：集成记录标识，用于错误上报。
    返回：
        解析后的 JSON 响应体（dict 或 list）；无法解析时返回空字典。
    抛出：
        网络错误或非 2xx 响应错误会在记录运行错误后重新抛出，
        由调用方转换为 tool_unavailable 结果。
    """
    headers = {}
    headers.update(_auth_headers(api_token, username, password))
    try:
        response = httpx.get(url, headers=headers, params=params, timeout=max(1.0, timeout_seconds))
        response.raise_for_status()
        body = response.json()
    except Exception as err:
        report_run_error(
            err,
            tool_name=tool_name,
            source="openobserve",
            component=component,
            method="httpx.get",
            extras={"url": url, "integration_id": integration_id},
        )
        raise
    return body if isinstance(body, (dict, list)) else {}
