"""PostgreSQL Table Stats Tool."""

from typing import Any

from app.integrations.postgresql import PostgreSQLConfig, get_table_stats
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
)
def get_postgresql_table_stats(
    host: str,
    database: str,
    schema_name: str = "public",
    port: int = 5432,
    username: str = "postgres",
    password: str = "",
    ssl_mode: str = "prefer",
) -> dict[str, Any]:
    """Fetch table statistics for a specific schema (default 'public')."""
    config = PostgreSQLConfig(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        ssl_mode=ssl_mode,
    )
    return get_table_stats(config, schema_name=schema_name)
