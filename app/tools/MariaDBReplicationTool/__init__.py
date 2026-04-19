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
    database: str,
    username: str,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
) -> dict[str, Any]:
    """Fetch replication status from SHOW ALL SLAVES STATUS."""
    config = MariaDBConfig(
        host=host, port=port, database=database,
        username=username, password=password, ssl=ssl,
    )
    return get_replication_status(config)
