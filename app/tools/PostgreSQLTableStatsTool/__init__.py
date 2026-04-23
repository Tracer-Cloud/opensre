"""PostgreSQL Table Stats Tool."""

from typing import Any

from app.integrations.postgresql import (
    get_table_stats,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_postgresql_table_stats",
    description="Retrieve PostgreSQL table statistics including size, row counts, index usage, and maintenance info.",
    source="postgresql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying large tables or rapid table growth during storage incidents",
        "Analyzing table scan patterns and index usage efficiency",
        "Checking table maintenance status like vacuum and analyze operations",
    ],
    is_available=postgresql_is_available,
    extract_params=postgresql_extract_params,
)
def get_postgresql_table_stats(
    host: str,
    database: str | None = None,
    schema_name: str = "public",
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch table statistics for a specific schema (default 'public')."""
    _db_defaulted = database is None
    if database is None:
        database = "postgres"
    config = resolve_postgresql_config(host=host, database=database, port=port)
    result = get_table_stats(config, schema_name=schema_name)
    if _db_defaulted:
        result["default_db_warning"] = (
            "WARNING: No database was specified; defaulted to 'postgres'. Results may not reflect application data."
        )
    return result
