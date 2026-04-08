"""ClickHouse Query Activity Tool."""

from typing import Any

from app.integrations.clickhouse import ClickHouseConfig, get_query_activity
from app.tools.tool_decorator import tool


@tool(
    name="get_clickhouse_query_activity",
    description="Retrieve recent query activity from a ClickHouse instance, including query duration, rows read, and memory usage.",
    source="clickhouse",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying slow or resource-heavy queries during an incident",
        "Checking recent query patterns that may correlate with performance issues",
        "Reviewing query activity after an alert fires",
    ],
)
def get_clickhouse_query_activity(
    host: str,
    port: int = 8123,
    database: str = "default",
    username: str = "default",
    password: str = "",
    secure: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Fetch recent completed queries from a ClickHouse instance."""
    config = ClickHouseConfig(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        secure=secure,
    )
    return get_query_activity(config, limit=limit)
