"""MySQL Table Stats Tool."""

from typing import Any

from app.integrations.mysql import get_table_stats, resolve_mysql_config
from app.tools.tool_decorator import tool


@tool(
    name="get_mysql_table_stats",
    description="Retrieve MySQL table statistics including row counts and data/index sizes from information_schema.",
    source="mysql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying the largest tables consuming storage during capacity incidents",
        "Reviewing table sizes and growth patterns for capacity planning",
        "Finding tables with unexpectedly high row counts or index overhead",
    ],
)
def get_mysql_table_stats(
    host: str,
    database: str,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch table statistics for all base tables in the target database."""
    config = resolve_mysql_config(host=host, database=database, port=port)
    return get_table_stats(config)
