"""MongoDB Replica Set Status Tool."""

from typing import Any

from app.integrations.mongodb import (
    MongoDBConfig,
    get_rs_status,
    mongodb_extract_params,
    mongodb_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_replica_status",
    description="Retrieve replica set status, member health, and oplog lag for a MongoDB instance.",
    source="mongodb",
    surfaces=("investigation", "chat"),
    is_available=mongodb_is_available,
    extract_params=mongodb_extract_params,
)
def get_mongodb_replica_status(
    connection_string: str,
    auth_source: str = "admin",
    tls: bool = True,
) -> dict[str, Any]:
    """Fetch status of all members in the MongoDB replica set."""
    config = MongoDBConfig(
        connection_string=connection_string,
        auth_source=auth_source,
        tls=tls,
    )
    return get_rs_status(config)
