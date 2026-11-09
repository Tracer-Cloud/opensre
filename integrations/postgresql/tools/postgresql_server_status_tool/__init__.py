"""PostgreSQL Server Status Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.postgresql import (
    get_server_status,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)


@tool(
    name="get_postgresql_server_status",
    description="Retrieve PostgreSQL server metrics including connections, transactions, cache hit ratio, and database statistics.",
    source="postgresql",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking PostgreSQL server health during an incident",
        "Identifying connection saturation or exhaustion issues",
        "Reviewing transaction rates and cache efficiency metrics",
    ],
    is_available=postgresql_is_available,
    injected_params=("host",),
    extract_params=postgresql_extract_params,
)
def get_postgresql_server_status(
    host: str,
    database: str | None = None,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch server status metrics from a PostgreSQL instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_server_status,
    )
