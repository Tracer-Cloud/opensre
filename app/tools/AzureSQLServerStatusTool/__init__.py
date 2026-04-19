"""Azure SQL Server Status Tool."""

from typing import Any

from app.integrations.azure_sql import get_server_status, resolve_azure_sql_config
from app.tools.tool_decorator import tool


_UNSET = object()

@tool(
    name="get_azure_sql_server_status",
    description="Retrieve Azure SQL Database server metrics including service tier, resource utilization, connections, and database size.",
    source="azure_sql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking Azure SQL Database health during an incident",
        "Identifying DTU/vCore throttling or resource exhaustion",
        "Reviewing service tier and connection saturation",
    ],
)
def get_azure_sql_server_status(
    server: str,
    database: object = _UNSET,
    port: int = 1433,
) -> dict[str, Any]:
    """Fetch server status metrics from an Azure SQL Database instance."""
    _db_defaulted = database is _UNSET
    if _db_defaulted:
        database = "master"
    config = resolve_azure_sql_config(server=server, database=database, port=port)
    result = get_server_status(config)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'master'. Results may not reflect application data."
    return result
