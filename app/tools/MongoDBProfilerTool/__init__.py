"""MongoDB Profiler Tool."""

from typing import Any

from app.integrations.mongodb import (
    MongoDBConfig,
    get_profiler_data,
    mongodb_database_is_available,
    mongodb_extract_params,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_profiler_data",
    description="Retrieve slow queries from the MongoDB database system.profile collection (requires profiling enabled).",
    source="mongodb",
    surfaces=("investigation", "chat"),
    is_available=mongodb_database_is_available,
    extract_params=mongodb_extract_params,
)
def get_mongodb_profiler_data(
    connection_string: str,
    database: str,
    threshold_ms: int = 100,
    auth_source: str = "admin",
    tls: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch recent slow query entries for a specific database."""
    config = MongoDBConfig(
        connection_string=connection_string,
        database=database,
        auth_source=auth_source,
        tls=tls,
    )
    return get_profiler_data(config, threshold_ms=threshold_ms, limit=limit)
