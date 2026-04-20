"""MySQL Replication Status Tool."""

from typing import Any

from app.integrations.mysql import (
    get_replication_status,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)
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
    is_available=mysql_is_available,
    extract_params=mysql_extract_params,
)
def get_mysql_replication_status(
    host: str,
    database: str | None = None,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch replication status from a MySQL instance."""
    _db_defaulted = database is None
    if database is None:
        database = "mysql"
    config = resolve_mysql_config(host=host, database=database, port=port)
    result = get_replication_status(config)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'mysql'. Results may not reflect application data."
    return result
