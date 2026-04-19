"""Azure SQL Slow Queries Tool."""

from typing import Any

from app.integrations.azure_sql import get_slow_queries, resolve_azure_sql_config
from app.tools.tool_decorator import tool


_UNSET = object()

@tool(
    name="get_azure_sql_slow_queries",
    description="Retrieve slow query statistics from Azure SQL Database query stats DMV, ordered by average elapsed time.",
    source="azure_sql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying queries with high average execution time",
        "Finding resource-intensive queries causing DTU throttling",
        "Reviewing query performance trends for capacity planning",
    ],
)
def get_azure_sql_slow_queries(
    server: str,
    database: object = _UNSET,
    port: int = 1433,
    threshold_ms: int = 1000,
) -> dict[str, Any]:
    """Fetch slow query statistics from an Azure SQL Database instance."""
    _db_defaulted = database is _UNSET
    if _db_defaulted:
        database = "master"
    config = resolve_azure_sql_config(server=server, database=database, port=port)
    result = get_slow_queries(config, threshold_ms=threshold_ms)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'master'. Results may not reflect application data."
    return result
