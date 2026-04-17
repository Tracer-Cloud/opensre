"""MariaDB Global Status Tool."""

from typing import Any

from app.integrations.mariadb import (
    MariaDBConfig,
    get_global_status,
    mariadb_extract_params,
    mariadb_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mariadb_global_status",
    description="Retrieve key MariaDB server metrics including connections, threads, slow queries, InnoDB buffer pool stats, and uptime from SHOW GLOBAL STATUS.",
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    extract_params=mariadb_extract_params,
)
def get_mariadb_global_status(
    host: str,
    database: str,
    username: str,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
) -> dict[str, Any]:
    """Fetch curated server metrics from SHOW GLOBAL STATUS."""
    config = MariaDBConfig(
        host=host, port=port, database=database,
        username=username, password=password, ssl=ssl,
    )
    return get_global_status(config)
