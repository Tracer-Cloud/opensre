"""PostgreSQL Current Queries Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.postgresql import (
    get_current_queries,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)


@tool(
    name="get_postgresql_current_queries",
    description=(
        "Retrieve currently executing PostgreSQL queries above a specific duration threshold."
    ),
    source="postgresql",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Identifying long-running queries that may be causing performance issues",
        "Investigating database locks and blocking queries during incidents",
        "Finding resource-intensive queries correlating with alert timeframes",
    ],
    is_available=postgresql_is_available,
    injected_params=("host",),
    extract_params=postgresql_extract_params,
)
def get_postgresql_current_queries(
    host: str,
    database: str | None = None,
    threshold_seconds: int = 1,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch currently running queries above the threshold (default 1 second)."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_current_queries(config, threshold_seconds=threshold_seconds),
    )
