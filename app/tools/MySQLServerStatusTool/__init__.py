"""MySQL Server Status Tool."""

from typing import Any

from app.integrations.mysql import get_server_status, resolve_mysql_config
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
)
def get_mysql_server_status(
    host: str,
    database: str,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch server status metrics from a MySQL instance."""
    config = resolve_mysql_config(host=host, database=database, port=port)
    return get_server_status(config)
