"""PostgreSQL Server Status Tool."""

from typing import Any

from app.integrations.postgresql import PostgreSQLConfig, get_server_status
from app.tools.tool_decorator import tool


@tool(
    name="get_postgresql_server_status",
    description="Retrieve PostgreSQL server metrics including connections, transactions, cache hit ratio, and database statistics.",
    source="postgresql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking PostgreSQL server health during an incident",
        "Identifying connection saturation or exhaustion issues",
        "Reviewing transaction rates and cache efficiency metrics",
    ],
)
def get_postgresql_server_status(
    host: str,
    database: str,
    port: int = 5432,
    username: str = "postgres",
    password: str = "",
    ssl_mode: str = "prefer",
) -> dict[str, Any]:
    """Fetch server status metrics from a PostgreSQL instance."""
    config = PostgreSQLConfig(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        ssl_mode=ssl_mode,
    )
    return get_server_status(config)
