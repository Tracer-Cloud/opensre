"""Azure SQL Slow Queries Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.azure_sql import (
    azure_sql_extract_params,
    azure_sql_is_available,
    get_slow_queries,
    resolve_azure_sql_config,
)


@tool(
    name="get_azure_sql_slow_queries",
    description=(
        "Retrieve slow query statistics from Azure SQL Database query stats DMV,"
        " ordered by average elapsed time."
    ),
    source="azure_sql",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Identifying queries with high average execution time",
        "Finding resource-intensive queries causing DTU throttling",
        "Reviewing query performance trends for capacity planning",
    ],
    is_available=azure_sql_is_available,
    injected_params=("server",),
    extract_params=azure_sql_extract_params,
)
def get_azure_sql_slow_queries(
    server: str,
    database: str | None = None,
    port: int = 1433,
    threshold_ms: int = 1000,
) -> dict[str, Any]:
    """Fetch slow query statistics from an Azure SQL Database instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="master",
        config_resolver=resolve_azure_sql_config,
        resolver_kwargs={"server": server, "port": port},
        db_caller=lambda config: get_slow_queries(config, threshold_ms=threshold_ms),
    )
