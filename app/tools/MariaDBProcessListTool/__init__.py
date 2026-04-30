"""MariaDB Process List Tool."""

from typing import Any

from app.integrations.mariadb import (
    MariaDBConfig,
    get_process_list,
    mariadb_extract_params,
    mariadb_is_available,
)
from app.tools.tool_decorator import tool
from app.tools.utils.sql_wrapper import call_db_tool_with_default_db_warning


@tool(
    name="get_mariadb_process_list",
    description=(
        "Retrieve active MariaDB threads and queries from"
        " information_schema.PROCESSLIST, excluding idle connections."
    ),
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    extract_params=mariadb_extract_params,
)
def get_mariadb_process_list(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
    max_results: int = 50,
) -> dict[str, Any]:
    """Fetch active threads from information_schema.PROCESSLIST."""

    def mariadb_config_builder(database: str) -> MariaDBConfig:
        return MariaDBConfig(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            ssl=ssl,
            max_results=max_results,
        )

    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=mariadb_config_builder,
        resolver_kwargs={},
        db_caller=get_process_list,
    )
