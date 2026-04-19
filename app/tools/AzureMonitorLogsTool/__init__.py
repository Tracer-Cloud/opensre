"""Azure Monitor Log Analytics tool with bounded read-only retrieval."""

from __future__ import annotations

from typing import Any

import httpx

from app.tools.tool_decorator import tool

_DEFAULT_MAX_RESULTS = 100
_MAX_HARD_LIMIT = 200


def _bounded_limit(limit: int, max_results: int) -> int:
    safe_max = max(1, min(max_results, _MAX_HARD_LIMIT))
    return max(1, min(limit, safe_max))


def _azure_available(sources: dict[str, dict[str, Any]]) -> bool:
    azure = sources.get("azure", {})
    return bool(azure.get("connection_verified") and azure.get("workspace_id") and azure.get("access_token"))


def _azure_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    azure = sources["azure"]
    return {
        "workspace_id": str(azure.get("workspace_id", "")).strip(),
        "access_token": str(azure.get("access_token", "")).strip(),
        "endpoint": str(azure.get("endpoint", "https://api.loganalytics.io")).strip(),
        "query": str(azure.get("query", "")).strip(),
        "time_range_minutes": int(azure.get("time_range_minutes", 60) or 60),
        "limit": 50,
        "max_results": int(azure.get("max_results", _DEFAULT_MAX_RESULTS) or _DEFAULT_MAX_RESULTS),
        "integration_id": str(azure.get("integration_id", "")).strip(),
    }


def _ensure_take_clause(query: str, limit: int) -> str:
    normalized = query.strip()
    if not normalized:
        return f"AppTraces | order by TimeGenerated desc | take {limit}"
    lowered = normalized.lower()
    if " take " in f" {lowered} ":
        return normalized
    return f"{normalized} | take {limit}"


@tool(
    name="query_azure_monitor_logs",
    description="Query Azure Monitor Log Analytics using a bounded KQL query.",
    source="azure",
    surfaces=("investigation", "chat"),
    requires=["workspace_id", "access_token"],
    input_schema={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string"},
            "access_token": {"type": "string"},
            "endpoint": {"type": "string", "default": "https://api.loganalytics.io"},
            "query": {"type": "string"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 50},
            "max_results": {"type": "integer", "default": 100},
            "integration_id": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 20.0},
        },
        "required": ["workspace_id", "access_token"],
    },
    is_available=_azure_available,
    extract_params=_azure_extract_params,
)
def query_azure_monitor_logs(
    workspace_id: str,
    access_token: str,
    endpoint: str = "https://api.loganalytics.io",
    query: str = "",
    time_range_minutes: int = 60,
    limit: int = 50,
    max_results: int = _DEFAULT_MAX_RESULTS,
    integration_id: str = "",
    timeout_seconds: float = 20.0,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch bounded rows from Azure Monitor Log Analytics."""
    workspace = workspace_id.strip()
    token = access_token.strip()
    if not workspace or not token:
        return {"source": "azure", "available": False, "error": "Missing Azure credentials.", "rows": []}

    effective_limit = _bounded_limit(limit, max_results)
    bounded_query = _ensure_take_clause(query, effective_limit)
    base_url = endpoint.strip().rstrip("/") or "https://api.loganalytics.io"
    url = f"{base_url}/v1/workspaces/{workspace}/query"
    payload = {
        "query": bounded_query,
        "timespan": f"PT{max(1, time_range_minutes)}M",
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=max(1.0, timeout_seconds))
        response.raise_for_status()
        body = response.json()
    except Exception as err:  # noqa: BLE001
        return {"source": "azure", "available": False, "error": str(err), "rows": []}

    tables = body.get("tables", []) if isinstance(body, dict) else []
    rows: list[dict[str, Any]] = []
    if tables and isinstance(tables, list) and isinstance(tables[0], dict):
        first_table = tables[0]
        columns = [
            str(column.get("name", "")).strip()
            for column in first_table.get("columns", [])
            if isinstance(column, dict)
        ]
        for raw_row in first_table.get("rows", []):
            if not isinstance(raw_row, list):
                continue
            rows.append({
                columns[idx]: raw_row[idx] if idx < len(raw_row) else None for idx in range(len(columns))
            })

    rows = rows[:effective_limit]
    return {
        "source": "azure",
        "available": True,
        "workspace_id": workspace,
        "integration_id": integration_id,
        "query": bounded_query,
        "total_returned": len(rows),
        "rows": rows,
    }
