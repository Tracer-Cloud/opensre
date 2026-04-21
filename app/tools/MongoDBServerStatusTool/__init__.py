"""MongoDB Server Status Tool."""

from typing import Any

from app.integrations.mongodb import (
    MongoDBConfig,
    get_server_status,
    mongodb_extract_params,
    mongodb_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_server_status",
    description="Retrieve high-level MongoDB server status including connections, memory usage, and operation counters.",
    source="mongodb",
    surfaces=("investigation", "chat"),
    is_available=mongodb_is_available,
    extract_params=mongodb_extract_params,
)
def get_mongodb_server_status(
    connection_string: str,
    auth_source: str = "admin",
    tls: bool = True,
) -> dict[str, Any]:
    """Fetch server status metrics from a MongoDB instance."""
    config = MongoDBConfig(
        connection_string=connection_string,
        auth_source=auth_source,
        tls=tls,
    )
    return get_server_status(config)
