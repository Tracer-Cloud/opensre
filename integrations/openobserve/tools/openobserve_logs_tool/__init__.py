"""OpenObserve log search tool with bounded read-only retrieval."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate

_DEFAULT_MAX_RESULTS = 100
_MAX_HARD_LIMIT = 200

#: Bound the SQL query text echoed into a report summary -- it can be a long
#: or multi-line query built by the caller, not just the default form.
_QUERY_SUMMARY_TRUNCATE_LEN = 80


def _bounded_limit(limit: int, max_results: int) -> int:
    safe_max = max(1, min(max_results, _MAX_HARD_LIMIT))
    return max(1, min(limit, safe_max))


def _map_query_openobserve_logs(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the row count, stream, and a bounded snippet of the query executed.

    ``records`` is truncated to ``effective_limit`` after the query runs, so
    a returned count at that ceiling may understate how many rows actually
    matched -- use the "N+" convention.
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


def _openobserve_available(sources: dict[str, dict[str, Any]]) -> bool:
    oo = sources.get("openobserve", {})
    has_token = bool(str(oo.get("api_token", "")).strip())
    has_user_password = bool(
        str(oo.get("username", "")).strip() and str(oo.get("password", "")).strip()
    )
    return bool(
        oo.get("connection_verified") and oo.get("base_url") and (has_token or has_user_password)
    )


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
    """Fetch bounded evidence from OpenObserve."""
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

    endpoint = f"{base}/api/{(org or 'default').strip()}/_search"
    payload: dict[str, Any] = {
        "query": {
            "sql": normalized_query,
            # OpenObserve's _search API takes start_time/end_time in
            # microseconds since epoch, not milliseconds — a millisecond
            # value silently matches nothing (no error) rather than raising,
            # since it resolves to a time range near the Unix epoch.
            "start_time": int(start.timestamp() * 1_000_000),
            "end_time": int(now.timestamp() * 1_000_000),
        },
        "size": effective_limit,
    }
    if stream_name:
        payload["stream_name"] = stream_name

    headers = {"Content-Type": "application/json"}
    headers.update(_auth_headers(auth_token, user, secret))

    try:
        response = httpx.post(
            endpoint, headers=headers, json=payload, timeout=max(1.0, timeout_seconds)
        )
        response.raise_for_status()
        body = response.json()
    except Exception as err:
        report_run_error(
            err,
            tool_name="query_openobserve_logs",
            source="openobserve",
            component="integrations.openobserve.tools.openobserve_logs_tool",
            method="httpx.post",
            extras={"endpoint": endpoint, "integration_id": integration_id},
        )
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
