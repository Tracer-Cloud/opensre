"""Azure SQL Wait Stats Tool."""

from typing import Any

from app.integrations.azure_sql import get_wait_stats, resolve_azure_sql_config
from app.tools.tool_decorator import tool


_UNSET = object()

@tool(
    name="get_azure_sql_wait_stats",
    description="Retrieve top wait statistics from Azure SQL Database to diagnose throttling, lock contention, IO bottlenecks, and network issues.",
    source="azure_sql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying the most impactful wait types during an incident",
        "Diagnosing lock contention or IO bottlenecks",
        "Understanding resource governance limits on Azure SQL",
    ],
)
def get_azure_sql_wait_stats(
    server: str,
    database: object = _UNSET,
    port: int = 1433,
) -> dict[str, Any]:
    """Fetch wait statistics from an Azure SQL Database instance."""
    _db_defaulted = database is _UNSET
    if _db_defaulted:
        database = "master"
    config = resolve_azure_sql_config(server=server, database=database, port=port)
    result = get_wait_stats(config)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'master'. Results may not reflect application data."
    return result
