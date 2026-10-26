"""ClickHouse System Health Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.clickhouse import (
    ClickHouseConfig,
    clickhouse_extract_params,
    clickhouse_is_available,
    get_system_health,
    get_table_stats,
)


def _map_get_clickhouse_system_health(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite server version/uptime and how many tables were surveyed."""
    if not output.get("available"):
        return
    version = output.get("version")
    uptime_seconds = output.get("uptime_seconds")
    table_stats = output.get("table_stats") or []
    parts = [
        part
        for part in (
            f"version {version}" if version else None,
            f"uptime {uptime_seconds}s" if uptime_seconds is not None else None,
            f"{len(table_stats)} table(s) surveyed" if table_stats else None,
        )
        if part
    ]
    if not parts:
        return
    record_evidence_entry(
        evidence,
        source="get_clickhouse_system_health",
        label="ClickHouse System Health",
        summary=", ".join(parts),
    )


@tool(
    name="get_clickhouse_system_health",
    description="Retrieve system health metrics and table statistics from a ClickHouse instance, including active queries, connections, and table sizes.",
    source="clickhouse",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking ClickHouse server health during an incident",
        "Identifying large or rapidly growing tables",
        "Reviewing connection and query counts for capacity issues",
    ],
    is_available=clickhouse_is_available,
    injected_params=("host",),
    extract_params=clickhouse_extract_params,
    evidence_mapper=_map_get_clickhouse_system_health,
)
def get_clickhouse_system_health(
    host: str,
    port: int = 8123,
    database: str = "default",
    username: str = "default",
    password: str = "",
    secure: bool = False,
    include_table_stats: bool = True,
) -> dict[str, Any]:
    """Fetch system health metrics and optionally table stats from ClickHouse."""
    config = ClickHouseConfig(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        secure=secure,
    )
    result = get_system_health(config)
    if include_table_stats and result.get("available"):
        table_result = get_table_stats(config, database=database)
        result["table_stats"] = table_result.get("tables", [])
    return result
