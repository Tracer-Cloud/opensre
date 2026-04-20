"""MariaDB Replication Status Tool."""

from typing import Any

from app.integrations.mariadb import (
    MariaDBConfig,
    get_replication_status,
    mariadb_extract_params,
    mariadb_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mariadb_replication_status",
    description="Retrieve MariaDB replication status including I/O and SQL thread state, lag, and errors from SHOW ALL SLAVES STATUS.",
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    extract_params=mariadb_extract_params,
)
def get_mariadb_replication_status(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
) -> dict[str, Any]:
    """Fetch replication status from SHOW ALL SLAVES STATUS."""
    _db_defaulted = database is None
    if database is None:
        database = "mysql"
    config = MariaDBConfig(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        ssl=ssl,
    )
    result = get_replication_status(config)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'mysql'. Results may not reflect application data."
    return result
