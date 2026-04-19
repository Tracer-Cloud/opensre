"""PostgreSQL Current Queries Tool."""

from typing import Any

from app.integrations.postgresql import get_current_queries, resolve_postgresql_config
from app.tools.tool_decorator import tool


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
)
def get_postgresql_current_queries(
    host: str,
    database: str,
    threshold_seconds: int = 1,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch currently running queries above the threshold (default 1 second)."""
    config = resolve_postgresql_config(host=host, database=database, port=port)
    return get_current_queries(config, threshold_seconds=threshold_seconds)
