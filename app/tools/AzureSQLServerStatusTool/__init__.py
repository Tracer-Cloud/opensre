"""Azure SQL Server Status Tool."""

from typing import Any

from app.integrations.azure_sql import (
    azure_sql_extract_params,
    azure_sql_is_available,
    get_server_status,
    resolve_azure_sql_config,
)
from app.tools.tool_decorator import tool


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
    is_available=azure_sql_is_available,
    extract_params=azure_sql_extract_params,
)
def get_azure_sql_server_status(
    server: str,
    database: str | None = None,
    port: int = 1433,
) -> dict[str, Any]:
    """Fetch server status metrics from an Azure SQL Database instance."""
    _db_defaulted = database is None
    if database is None:
        database = "master"
    config = resolve_azure_sql_config(server=server, database=database, port=port)
    result = get_server_status(config)
    if _db_defaulted:
        result["default_db_warning"] = (
            "WARNING: No database was specified; defaulted to 'master'. Results may not reflect application data."
        )
    return result
