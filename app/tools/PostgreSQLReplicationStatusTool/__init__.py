"""PostgreSQL Replication Status Tool."""

from typing import Any

from app.integrations.postgresql import get_replication_status, resolve_postgresql_config
from app.tools.tool_decorator import tool


@tool(
    name="get_postgresql_replication_status",
    description="Retrieve PostgreSQL replication status including replica lag, WAL positions, and streaming status.",
    source="postgresql",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Investigating replication lag issues during database incidents",
        "Checking replica health and synchronization status",
        "Monitoring WAL streaming and replica connectivity problems",
    ],
)
def get_postgresql_replication_status(
    host: str,
    database: str,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch replication status from a PostgreSQL primary server."""
    config = resolve_postgresql_config(host=host, database=database, port=port)
    return get_replication_status(config)
