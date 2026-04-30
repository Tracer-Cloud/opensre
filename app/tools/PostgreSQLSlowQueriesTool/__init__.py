"""PostgreSQL Slow Queries Tool."""

from typing import Any

from app.integrations.postgresql import (
    get_slow_queries,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)
from app.tools.tool_decorator import tool
from app.tools.utils.sql_wrapper import call_db_tool_with_default_db_warning


@tool(
    name="get_postgresql_slow_queries",
    description=(
        "Retrieve slow PostgreSQL queries from pg_stat_statements extension, ranked"
        " by mean execution time."
    ),
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
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_slow_queries(config, threshold_ms=threshold_ms),
    )
