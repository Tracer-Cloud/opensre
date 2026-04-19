"""MySQL Replication Status Tool."""

from typing import Any

from app.integrations.mysql import get_replication_status, resolve_mysql_config
from app.tools.tool_decorator import tool


_UNSET = object()

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
    database: object = _UNSET,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch replication status from a MySQL instance."""
    _db_defaulted = database is _UNSET
    if _db_defaulted:
        database = "mysql"
    config = resolve_mysql_config(host=host, database=database, port=port)
    result = get_replication_status(config)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'mysql'. Results may not reflect application data."
    return result
