"""OpenObserve log search tool with bounded read-only retrieval."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.tools.tool_decorator import tool

_DEFAULT_MAX_RESULTS = 100
_MAX_HARD_LIMIT = 200


def _bounded_limit(limit: int, max_results: int) -> int:
    safe_max = max(1, min(max_results, _MAX_HARD_LIMIT))
    return max(1, min(limit, safe_max))


def _openobserve_available(sources: dict[str, dict[str, Any]]) -> bool:
    oo = sources.get("openobserve", {})
    has_token = bool(str(oo.get("api_token", "")).strip())
    has_user_password = bool(str(oo.get("username", "")).strip() and str(oo.get("password", "")).strip())
    return bool(oo.get("connection_verified") and oo.get("base_url") and (has_token or has_user_password))


def _openobserve_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
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


def _auth_headers(api_token: str, username: str, password: str) -> dict[str, str]:
    if api_token:
        return {"Authorization": f"Bearer {api_token}"}
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {credentials}"}


def _extract_records(body: dict[str, Any]) -> list[dict[str, Any]]:
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


@tool(
    name="query_openobserve_logs",
    description="Query OpenObserve logs using bounded read-only search.",
    source="openobserve",
    surfaces=("investigation", "chat"),
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
    extract_params=_openobserve_extract_params,
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
    """Fetch bounded evidence from OpenObserve."""
    base = base_url.strip().rstrip("/")
    if not base:
        return {"source": "openobserve", "available": False, "error": "Missing OpenObserve URL.", "records": []}
    auth_token = api_token.strip()
    user = username.strip()
    secret = password.strip()
    if not auth_token and not (user and secret):
        return {"source": "openobserve", "available": False, "error": "Missing OpenObserve credentials.", "records": []}

    effective_limit = _bounded_limit(limit, max_results)
    now = datetime.now(UTC)
    start = now - timedelta(minutes=max(1, time_range_minutes))
    normalized_query = query.strip() or (
        "SELECT * FROM \"default\" WHERE level = 'error' ORDER BY _timestamp DESC"
    )

    endpoint = f"{base}/api/{(org or 'default').strip()}/_search"
    payload: dict[str, Any] = {
        "query": {
            "sql": normalized_query,
            "start_time": int(start.timestamp() * 1000),
            "end_time": int(now.timestamp() * 1000),
        },
        "size": effective_limit,
    }
    if stream.strip():
        payload["stream_name"] = stream.strip()

    headers = {"Content-Type": "application/json"}
    headers.update(_auth_headers(auth_token, user, secret))

    try:
        response = httpx.post(endpoint, headers=headers, json=payload, timeout=max(1.0, timeout_seconds))
        response.raise_for_status()
        body = response.json()
    except Exception as err:  # noqa: BLE001
        return {"source": "openobserve", "available": False, "error": str(err), "records": []}

    records = _extract_records(body if isinstance(body, dict) else {})[:effective_limit]
    return {
        "source": "openobserve",
        "available": True,
        "org": (org or "default").strip() or "default",
        "stream": stream.strip(),
        "integration_id": integration_id,
        "query": normalized_query,
        "total_returned": len(records),
        "records": records,
    }
