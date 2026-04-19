"""Better Stack Telemetry Logs Tool."""

from __future__ import annotations

from typing import Any

from app.integrations.betterstack import (
    BetterStackConfig,
    betterstack_extract_params,
    betterstack_is_available,
    query_logs,
)
from app.tools.tool_decorator import tool


@tool(
    name="query_betterstack_logs",
    description=(
        "Query a Better Stack Telemetry source table for recent log rows "
        "using ClickHouse SQL over HTTP. Returns (dt, raw) pairs from "
        "remote(table), optionally bounded by ISO-8601 since/until timestamps."
    ),
    source="betterstack",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Fetching recent application log lines from a Better Stack source during RCA",
        "Correlating timestamped log events with an alert window",
        "Scanning a specific source table (e.g. t123456_myapp_logs) for recent activity",
    ],
    is_available=betterstack_is_available,
    extract_params=betterstack_extract_params,
)
def query_betterstack_logs(
    query_endpoint: str,
    username: str,
    password: str = "",
    tables: list[str] | None = None,
    table: str = "",
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Query recent log rows from a Better Stack source table.

    ``query_endpoint`` / ``username`` / ``password`` / ``tables`` are sourced
    automatically from the Better Stack integration via ``extract_params``.
    The planner supplies ``table`` (a single table to query); when it omits
    ``table`` we fall back to the first entry in the configured ``tables``
    hint list, or return a structured error if nothing is configured.
    """
    effective_table = (table or "").strip()
    if not effective_table and tables:
        effective_table = next((t for t in tables if t), "")

    config = BetterStackConfig(
        query_endpoint=query_endpoint,
        username=username,
        password=password,
        tables=list(tables or []),
    )
    return query_logs(
        config,
        effective_table,
        since=since,
        until=until,
        limit=limit,
    )
