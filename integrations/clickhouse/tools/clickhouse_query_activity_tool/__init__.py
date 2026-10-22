"""ClickHouse Query Activity Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.clickhouse import (
    ClickHouseConfig,
    clickhouse_extract_params,
    clickhouse_is_available,
    get_query_activity,
)


def _map_get_clickhouse_query_activity(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite recent query activity, noting how many of the returned queries failed."""
    if not output.get("available"):
        return
    queries = output.get("queries") or []
    if not queries:
        return
    failed = sum(
        1
        for query in queries
        if isinstance(query, dict) and "Exception" in str(query.get("type", ""))
    )
    summary = f"{len(queries)} quer{'y' if len(queries) == 1 else 'ies'}"
    if failed:
        summary += f", {failed} failed"
    record_evidence_entry(
        evidence,
        source="get_clickhouse_query_activity",
        label="ClickHouse Query Activity",
        summary=summary,
    )


@tool(
    name="get_clickhouse_query_activity",
    description="Retrieve recent query activity (including failed queries) from a ClickHouse instance, with query duration, rows read, and memory usage.",
    source="clickhouse",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Identifying slow or resource-heavy queries during an incident",
        "Checking recent query patterns that may correlate with performance issues",
        "Reviewing query activity after an alert fires",
    ],
    is_available=clickhouse_is_available,
    injected_params=("host",),
    extract_params=clickhouse_extract_params,
    evidence_mapper=_map_get_clickhouse_query_activity,
)
def get_clickhouse_query_activity(
    host: str,
    port: int = 8123,
    database: str = "default",
    username: str = "default",
    password: str = "",
    secure: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Fetch recent completed queries from a ClickHouse instance."""
    config = ClickHouseConfig(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        secure=secure,
    )
    return get_query_activity(config, limit=limit)
