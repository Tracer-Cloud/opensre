"""MySQL Server Status Tool."""

from typing import Any

from app.integrations.mysql import (
    get_server_status,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mysql_server_status",
    description="Retrieve MySQL server metrics including connections, uptime, query rates, and InnoDB buffer pool statistics.",
    source="mysql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking MySQL server health during an incident",
        "Identifying connection saturation or exhaustion issues",
        "Reviewing InnoDB buffer pool hit ratio and deadlock counts",
    ],
    is_available=mysql_is_available,
    extract_params=mysql_extract_params,
)
def get_mysql_server_status(
    host: str,
    database: str | None = None,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch server status metrics from a MySQL instance."""
    _db_defaulted = database is None
    if database is None:
        database = "mysql"
    config = resolve_mysql_config(host=host, database=database, port=port)
    result = get_server_status(config)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'mysql'. Results may not reflect application data."
    return result
