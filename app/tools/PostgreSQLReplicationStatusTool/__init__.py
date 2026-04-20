"""PostgreSQL Replication Status Tool."""

from typing import Any

from app.integrations.postgresql import (
    get_replication_status,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)
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
    is_available=postgresql_is_available,
    extract_params=postgresql_extract_params,
)
def get_postgresql_replication_status(
    host: str,
    database: str | None = None,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch replication status from a PostgreSQL primary server."""
    _db_defaulted = database is None
    if database is None:
        database = "postgres"
    config = resolve_postgresql_config(host=host, database=database, port=port)
    result = get_replication_status(config)
    if _db_defaulted:
        result["default_db_warning"] = (
            "WARNING: No database was specified; defaulted to 'postgres'. Results may not reflect application data."
        )
    return result
