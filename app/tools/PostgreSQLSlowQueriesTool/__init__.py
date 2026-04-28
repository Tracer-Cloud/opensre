"""PostgreSQL Slow Queries Tool."""

from typing import Any

from app.integrations.postgresql import (
    get_slow_queries,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)
from app.tools.tool_decorator import tool
from app.tools.utils.db_warnings import default_db_warning


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
    is_available=postgresql_is_available,
    extract_params=postgresql_extract_params,
)
def get_postgresql_slow_queries(
    host: str,
    database: str | None = None,
    threshold_ms: int = 1000,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch slow query statistics above the threshold (default 1000ms mean time)."""
    _db_defaulted = database is None
    if database is None:
        database = "postgres"
    config = resolve_postgresql_config(host=host, database=database, port=port)
    result = get_slow_queries(config, threshold_ms=threshold_ms)
    if _db_defaulted:
        result["default_db_warning"] = default_db_warning("postgres")
    return result
