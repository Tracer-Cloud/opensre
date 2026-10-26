"""Azure SQL Resource Stats Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.azure_sql import (
    azure_sql_extract_params,
    azure_sql_is_available,
    get_resource_stats,
    resolve_azure_sql_config,
)


@tool(
    name="get_azure_sql_resource_stats",
    description="Retrieve Azure SQL Database resource utilization history (CPU, IO, log throughput, memory) with throttling risk assessment.",
    source="azure_sql",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Diagnosing DTU/vCore throttling on Azure SQL Database",
        "Identifying resource saturation causing query timeouts",
        "Reviewing historical resource trends to determine if tier upgrade is needed",
    ],
    is_available=azure_sql_is_available,
    injected_params=("server",),
    extract_params=azure_sql_extract_params,
)
def get_azure_sql_resource_stats(
    server: str,
    database: str | None = None,
    port: int = 1433,
    minutes: int = 30,
) -> dict[str, Any]:
    """Fetch resource utilization stats from an Azure SQL Database instance."""
    _db_defaulted = database is None
    if database is None:
        database = "master"
    config = resolve_azure_sql_config(server=server, database=database, port=port)
    result = get_resource_stats(config, minutes=minutes)
    if _db_defaulted:
        result["default_db_warning"] = (
            "WARNING: No database was specified; defaulted to 'master'. Results may not reflect application data."
        )
    return result
