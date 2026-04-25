"""PostgreSQL Current Queries Tool."""

from typing import Any

from app.integrations.postgresql import (
    get_current_queries,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)
from app.tools.tool_decorator import tool
from app.tools.utils.sql import sql_tool_flow


@tool(
    name="get_postgresql_current_queries",
    description="Retrieve currently executing PostgreSQL queries above a specific duration threshold.",
    source="postgresql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying long-running queries that may be causing performance issues",
        "Investigating database locks and blocking queries during incidents",
        "Finding resource-intensive queries correlating with alert timeframes",
    ],
    is_available=postgresql_is_available,
    extract_params=postgresql_extract_params,
)
def get_postgresql_current_queries(
    host: str,
    database: str | None = None,
    threshold_seconds: int = 1,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch currently running queries above the threshold (default 1 second)."""
    return sql_tool_flow(
        database=database,
        default_db="postgres",
        resolve_func=resolve_postgresql_config,
        execute_func=get_current_queries,
        identifying_params={"host": host, "port": port},
        execute_params={"threshold_seconds": threshold_seconds},
    )
