"""Azure SQL Resource Stats Tool."""

from typing import Any

from app.integrations.azure_sql import get_resource_stats, resolve_azure_sql_config
from app.tools.tool_decorator import tool


_UNSET = object()

@tool(
    name="get_azure_sql_resource_stats",
    description="Retrieve Azure SQL Database resource utilization history (CPU, IO, log throughput, memory) with throttling risk assessment.",
    source="azure_sql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Diagnosing DTU/vCore throttling on Azure SQL Database",
        "Identifying resource saturation causing query timeouts",
        "Reviewing historical resource trends to determine if tier upgrade is needed",
    ],
)
def get_azure_sql_resource_stats(
    server: str,
    database: object = _UNSET,
    port: int = 1433,
    minutes: int = 30,
) -> dict[str, Any]:
    """Fetch resource utilization stats from an Azure SQL Database instance."""
    _db_defaulted = database is _UNSET
    if _db_defaulted:
        database = "master"
    config = resolve_azure_sql_config(server=server, database=database, port=port)
    result = get_resource_stats(config, minutes=minutes)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'master'. Results may not reflect application data."
    return result
