"""MySQL Current Processes Tool."""

from typing import Any

from app.integrations.mysql import get_current_processes, resolve_mysql_config
from app.tools.tool_decorator import tool


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
)
def get_mysql_current_processes(
    host: str,
    database: str,
    threshold_seconds: int = 1,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch active processes running longer than threshold_seconds (default 1s)."""
    config = resolve_mysql_config(host=host, database=database, port=port)
    return get_current_processes(config, threshold_seconds=threshold_seconds)
