"""MySQL Replication Status Tool."""

from typing import Any

from app.integrations.mysql import get_replication_status, resolve_mysql_config
from app.tools.tool_decorator import tool


@tool(
    name="get_mysql_replication_status",
    description="Retrieve MySQL replication status including IO/SQL thread health and replica lag.",
    source="mysql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking replica lag during high-write incidents",
        "Verifying replication IO and SQL threads are running",
        "Diagnosing replication errors and identifying last error details",
    ],
)
def get_mysql_replication_status(
    host: str,
    database: str,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch replication status from a MySQL instance."""
    config = resolve_mysql_config(host=host, database=database, port=port)
    return get_replication_status(config)
