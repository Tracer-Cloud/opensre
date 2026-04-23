"""ClickHouse System Health Tool."""

from typing import Any

from app.integrations.clickhouse import (
    ClickHouseConfig,
    clickhouse_extract_params,
    clickhouse_is_available,
    get_system_health,
    get_table_stats,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_clickhouse_system_health",
    description="Retrieve system health metrics and table statistics from a ClickHouse instance, including active queries, connections, and table sizes.",
    source="clickhouse",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking ClickHouse server health during an incident",
        "Identifying large or rapidly growing tables",
        "Reviewing connection and query counts for capacity issues",
    ],
    is_available=clickhouse_is_available,
    extract_params=clickhouse_extract_params,
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
