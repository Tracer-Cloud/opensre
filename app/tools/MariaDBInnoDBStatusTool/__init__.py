"""MariaDB InnoDB Status Tool."""

from typing import Any

from app.integrations.mariadb import (
    MariaDBConfig,
    get_innodb_status,
    mariadb_extract_params,
    mariadb_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mariadb_innodb_status",
    description="Retrieve InnoDB engine internals including deadlocks, buffer pool state, and I/O activity from SHOW ENGINE INNODB STATUS.",
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    extract_params=mariadb_extract_params,
)
def get_mariadb_innodb_status(
    host: str,
    username: str,
    database: str = "mysql",
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
) -> dict[str, Any]:
    """Fetch InnoDB engine status."""
    config = MariaDBConfig(
        host=host, port=port, database=database,
        username=username, password=password, ssl=ssl,
    )
    result = get_innodb_status(config)
    if database == "mysql":
        result["note"] = "WARNING: Queried default system database ('mysql') because no database was specified. Results may not reflect application data."
    return result
