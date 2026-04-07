"""PostgreSQL Current Queries Tool."""

from typing import Any

from app.integrations.postgresql import PostgreSQLConfig, get_current_queries
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
    username: str = "postgres",
    password: str = "",
    ssl_mode: str = "prefer",
) -> dict[str, Any]:
    """Fetch currently running queries above the threshold (default 1 second)."""
    config = PostgreSQLConfig(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        ssl_mode=ssl_mode,
    )
    return get_current_queries(config, threshold_seconds=threshold_seconds)
