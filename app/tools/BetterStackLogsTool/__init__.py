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
        "Query a Better Stack Telemetry source for log rows using ClickHouse "
        "SQL over HTTP. Returns (dt, raw) pairs by UNIONing recent logs from "
        "remote(<source>_logs) with historical logs from s3Cluster(primary, "
        "<source>_s3) WHERE _row_type = 1, optionally bounded by since/until "
        "timestamps (ISO 8601)."
    ),
    source="betterstack",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Fetching application log lines from a Better Stack source during RCA",
        "Correlating timestamped log events with an alert window",
        "Scanning a specific source (e.g. t123456_myapp) for recent and archived activity",
    ],
    is_available=betterstack_is_available,
    extract_params=betterstack_extract_params,
)
def query_betterstack_logs(
    query_endpoint: str,
    username: str,
    password: str = "",
    sources: list[str] | None = None,
    source: str = "",
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Query log rows from a Better Stack source (recent + historical).

    ``query_endpoint`` / ``username`` / ``password`` / ``sources`` are sourced
    automatically from the Better Stack integration via ``extract_params``.
    The planner supplies ``source`` (a single source identifier to query);
    when it omits ``source`` we fall back to the first entry in the configured
    ``sources`` hint list, or return a structured error if nothing is configured.

    The ``source`` argument is the base identifier (e.g. ``t123456_myapp``);
    the integration appends ``_logs`` and ``_s3`` internally to build the
    ``remote(...)`` and ``s3Cluster(primary, ...)`` table functions.
    """
    effective_source = (source or "").strip()
    if not effective_source and sources:
        effective_source = next((s for s in sources if s), "")

    config = BetterStackConfig(
        query_endpoint=query_endpoint,
        username=username,
        password=password,
        sources=list(sources or []),
    )
    return query_logs(
        config,
        effective_source,
        since=since,
        until=until,
        limit=limit,
    )
