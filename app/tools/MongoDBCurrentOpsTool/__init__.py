"""MongoDB Current Ops Tool."""

from typing import Any

from app.integrations.mongodb import (
    MongoDBConfig,
    get_current_ops,
    mongodb_extract_params,
    mongodb_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_current_ops",
    description="Retrieve currently executing MongoDB operations above a specific duration threshold.",
    source="mongodb",
    surfaces=("investigation", "chat"),
    is_available=mongodb_is_available,
    extract_params=mongodb_extract_params,
)
def get_mongodb_current_ops(
    connection_string: str,
    threshold_ms: int = 1000,
    auth_source: str = "admin",
    tls: bool = True,
) -> dict[str, Any]:
    """Fetch currently running operations above the threshold (default 1000ms)."""
    config = MongoDBConfig(
        connection_string=connection_string,
        auth_source=auth_source,
        tls=tls,
    )
    return get_current_ops(config, threshold_ms=threshold_ms)
