"""Azure SQL Current Queries Tool."""

from typing import Any

from app.integrations.azure_sql import (
    azure_sql_extract_params,
    azure_sql_is_available,
    get_current_queries,
    resolve_azure_sql_config,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_azure_sql_current_queries",
    description="Retrieve currently running queries on Azure SQL Database above a duration threshold, including wait types and resource usage.",
    source="azure_sql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying long-running queries causing lock contention",
        "Diagnosing blocking chains during an Azure SQL incident",
        "Finding queries consuming excessive CPU or IO",
    ],
    is_available=azure_sql_is_available,
    extract_params=azure_sql_extract_params,
)
def get_azure_sql_current_queries(
    server: str,
    database: str | None = None,
    port: int = 1433,
    threshold_seconds: int = 1,
) -> dict[str, Any]:
    """Fetch currently running queries from an Azure SQL Database instance."""
    _db_defaulted = database is None
    if database is None:
        database = "master"
    config = resolve_azure_sql_config(server=server, database=database, port=port)
    result = get_current_queries(config, threshold_seconds=threshold_seconds)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'master'. Results may not reflect application data."
    return result
