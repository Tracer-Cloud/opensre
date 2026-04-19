"""PostgreSQL Server Status Tool."""

from typing import Any

from app.integrations.postgresql import get_server_status, resolve_postgresql_config
from app.tools.tool_decorator import tool


_UNSET = object()

@tool(
    name="get_postgresql_server_status",
    description="Retrieve PostgreSQL server metrics including connections, transactions, cache hit ratio, and database statistics.",
    source="postgresql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking PostgreSQL server health during an incident",
        "Identifying connection saturation or exhaustion issues",
        "Reviewing transaction rates and cache efficiency metrics",
    ],
)
def get_postgresql_server_status(
    host: str,
    database: object = _UNSET,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch server status metrics from a PostgreSQL instance."""
    _db_defaulted = database is _UNSET
    if _db_defaulted:
        database = "postgres"
    config = resolve_postgresql_config(host=host, database=database, port=port)
    result = get_server_status(config)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'postgres'. Results may not reflect application data."
    return result
