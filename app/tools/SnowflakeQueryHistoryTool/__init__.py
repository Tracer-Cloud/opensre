"""Snowflake query history tool with bounded read-only retrieval."""

from __future__ import annotations

from typing import Any

import httpx

from app.tools.tool_decorator import tool

_DEFAULT_MAX_RESULTS = 50
_MAX_HARD_LIMIT = 200


def _bounded_limit(limit: int, max_results: int) -> int:
    safe_max = max(1, min(max_results, _MAX_HARD_LIMIT))
    return max(1, min(limit, safe_max))


def _snowflake_available(sources: dict[str, dict[str, Any]]) -> bool:
    sf = sources.get("snowflake", {})
    has_token = bool(str(sf.get("token", "")).strip())
    return bool(sf.get("connection_verified") and sf.get("account_identifier") and has_token)


def _snowflake_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sf = sources["snowflake"]
    return {
        "account_identifier": str(sf.get("account_identifier", "")).strip(),
        "user": str(sf.get("user", "")).strip(),
        "password": str(sf.get("password", "")).strip(),
        "token": str(sf.get("token", "")).strip(),
        "warehouse": str(sf.get("warehouse", "")).strip(),
        "role": str(sf.get("role", "")).strip(),
        "database": str(sf.get("database", "")).strip(),
        "schema": str(sf.get("schema", "")).strip(),
        "query": str(sf.get("query", "")).strip(),
        "limit": 50,
        "max_results": int(sf.get("max_results", _DEFAULT_MAX_RESULTS) or _DEFAULT_MAX_RESULTS),
        "integration_id": str(sf.get("integration_id", "")).strip(),
    }


def _default_query(limit: int) -> str:
    return (
        "SELECT query_id, user_name, warehouse_name, execution_status, "
        "start_time, end_time, total_elapsed_time "
        "FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(RESULT_LIMIT => "
        f"{limit})) ORDER BY start_time DESC"
    )


def _ensure_sql_limit(query: str, limit: int) -> str:
    normalized = query.strip().rstrip(";")
    if not normalized:
        return _default_query(limit)
    lowered = normalized.lower()
    if " limit " in f" {lowered} ":
        return normalized
    return f"{normalized} LIMIT {limit}"


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _normalize_rows(response_payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = response_payload.get("data", [])
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data

    columns: list[str] = []
    metadata = response_payload.get("resultSetMetaData", {})
    row_type = metadata.get("rowType", []) if isinstance(metadata, dict) else []
    if isinstance(row_type, list):
        for column in row_type:
            if isinstance(column, dict):
                columns.append(str(column.get("name", "")).strip())

    if isinstance(data, list) and data and isinstance(data[0], list) and columns:
        rows: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, list):
                continue
            rows.append({
                columns[idx]: row[idx] if idx < len(row) else None for idx in range(len(columns))
            })
        return rows

    return []


@tool(
    name="query_snowflake_history",
    description="Query Snowflake query history using a read-only bounded statement.",
    source="snowflake",
    surfaces=("investigation", "chat"),
    requires=["account_identifier"],
    input_schema={
        "type": "object",
        "properties": {
            "account_identifier": {"type": "string"},
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
            "max_results": {"type": "integer", "default": 50},
            "user": {"type": "string"},
            "password": {"type": "string"},
            "token": {"type": "string"},
            "warehouse": {"type": "string"},
            "role": {"type": "string"},
            "database": {"type": "string"},
            "schema": {"type": "string"},
            "integration_id": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 20.0},
        },
        "required": ["account_identifier"],
    },
    is_available=_snowflake_available,
    extract_params=_snowflake_extract_params,
)
def query_snowflake_history(
    account_identifier: str,
    query: str = "",
    limit: int = 50,
    max_results: int = _DEFAULT_MAX_RESULTS,
    user: str = "",
    password: str = "",
    token: str = "",
    warehouse: str = "",
    role: str = "",
    database: str = "",
    schema: str = "",
    integration_id: str = "",
    timeout_seconds: float = 20.0,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch bounded query-history evidence from Snowflake SQL API."""
    _ = (user, password)
    effective_limit = _bounded_limit(limit, max_results)
    account = account_identifier.strip()
    bearer = token.strip()
    if not account:
        return {"source": "snowflake", "available": False, "error": "Missing account identifier.", "rows": []}
    if not bearer:
        return {"source": "snowflake", "available": False, "error": "Missing Snowflake token.", "rows": []}

    statement = _ensure_sql_limit(query, effective_limit)
    endpoint = f"https://{account}.snowflakecomputing.com/api/v2/statements"
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(_auth_header(bearer))

    payload: dict[str, Any] = {
        "statement": statement,
        "timeout": max(1, int(timeout_seconds)),
    }
    if warehouse:
        payload["warehouse"] = warehouse
    if role:
        payload["role"] = role
    if database:
        payload["database"] = database
    if schema:
        payload["schema"] = schema

    try:
        response = httpx.post(endpoint, headers=headers, json=payload, timeout=max(1.0, timeout_seconds))
        response.raise_for_status()
        body = response.json()
    except Exception as err:  # noqa: BLE001
        return {"source": "snowflake", "available": False, "error": str(err), "rows": []}

    rows = _normalize_rows(body)[:effective_limit]
    return {
        "source": "snowflake",
        "available": True,
        "account_identifier": account,
        "integration_id": integration_id,
        "query": statement,
        "total_returned": len(rows),
        "rows": rows,
    }
