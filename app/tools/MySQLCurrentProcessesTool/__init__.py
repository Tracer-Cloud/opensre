"""MySQL Current Processes Tool."""

from typing import Any

from app.integrations.mysql import (
    get_current_processes,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)
from app.tools.tool_decorator import tool
from app.tools.utils.db_warnings import default_db_warning


@tool(
    name="get_mysql_current_processes",
    description="Retrieve currently active MySQL processes above a duration threshold, excluding sleeping connections.",
    source="mysql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying long-running queries blocking other operations",
        "Investigating lock contention or deadlock situations",
        "Spotting runaway queries during an incident",
    ],
    is_available=mysql_is_available,
    extract_params=mysql_extract_params,
)
def get_mysql_current_processes(
    host: str,
    database: str | None = None,
    threshold_seconds: int = 1,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch active processes running longer than threshold_seconds (default 1s)."""
    _db_defaulted = database is None
    if database is None:
        database = "mysql"
    config = resolve_mysql_config(host=host, database=database, port=port)
    result = get_current_processes(config, threshold_seconds=threshold_seconds)
    if _db_defaulted:
        result["default_db_warning"] = default_db_warning("mysql")
    return result
