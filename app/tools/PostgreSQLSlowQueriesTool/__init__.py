"""PostgreSQL Slow Queries Tool."""

from typing import Any

from app.integrations.postgresql import get_slow_queries, resolve_postgresql_config
from app.tools.tool_decorator import tool


@tool(
    name="get_postgresql_slow_queries",
    description="Retrieve slow PostgreSQL queries from pg_stat_statements extension, ranked by mean execution time.",
    source="postgresql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying slow queries that may be causing performance degradation",
        "Analyzing query execution patterns during incident timeframes",
        "Finding poorly optimized queries with high execution times or low cache hit rates",
    ],
)
def get_postgresql_slow_queries(
    host: str,
    database: str,
    threshold_ms: int = 1000,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch slow query statistics above the threshold (default 1000ms mean time)."""
    config = resolve_postgresql_config(host=host, database=database, port=port)
    return get_slow_queries(config, threshold_ms=threshold_ms)
