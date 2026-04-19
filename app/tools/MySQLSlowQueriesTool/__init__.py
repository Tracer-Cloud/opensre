"""MySQL Slow Queries Tool."""

from typing import Any

from app.integrations.mysql import get_slow_queries, resolve_mysql_config
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
)
def get_mysql_slow_queries(
    host: str,
    database: str,
    threshold_ms: float = 1000.0,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch slow query statistics above threshold_ms mean execution time (default 1000ms)."""
    config = resolve_mysql_config(host=host, database=database, port=port)
    return get_slow_queries(config, threshold_ms=threshold_ms)
