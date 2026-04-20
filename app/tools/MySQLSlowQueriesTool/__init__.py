"""MySQL Slow Queries Tool."""

from typing import Any

from app.integrations.mysql import (
    get_slow_queries,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mysql_slow_queries",
    description="Retrieve slow MySQL queries from performance_schema, ranked by average execution time.",
    source="mysql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying slow queries that may be causing performance degradation",
        "Analyzing query execution patterns during incident timeframes",
        "Finding poorly optimized queries with high execution times or full-table scans",
    ],
    is_available=mysql_is_available,
    extract_params=mysql_extract_params,
)
def get_mysql_slow_queries(
    host: str,
    database: str | None = None,
    threshold_ms: float = 1000.0,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch slow query statistics above threshold_ms mean execution time (default 1000ms)."""
    _db_defaulted = database is None
    if database is None:
        database = "mysql"
    config = resolve_mysql_config(host=host, database=database, port=port)
    result = get_slow_queries(config, threshold_ms=threshold_ms)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'mysql'. Results may not reflect application data."
    return result
