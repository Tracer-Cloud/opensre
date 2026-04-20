"""MariaDB Slow Queries Tool."""

from typing import Any

from app.integrations.mariadb import (
    MariaDBConfig,
    get_slow_queries,
    mariadb_extract_params,
    mariadb_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mariadb_slow_queries",
    description="Retrieve top MariaDB queries by average execution time from performance_schema.events_statements_summary_by_digest.",
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    extract_params=mariadb_extract_params,
)
def get_mariadb_slow_queries(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
    max_results: int = 50,
) -> dict[str, Any]:
    """Fetch slow queries from performance_schema."""
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
        max_results=max_results,
    )
    result = get_slow_queries(config)
    if _db_defaulted:
        result["default_db_warning"] = "WARNING: No database was specified; defaulted to 'mysql'. Results may not reflect application data."
    return result
