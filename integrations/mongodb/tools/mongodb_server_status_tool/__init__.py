"""MongoDB Server Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.mongodb import (
    MongoDBConfig,
    get_server_status,
    mongodb_extract_params,
    mongodb_is_available,
)


def _map_get_mongodb_server_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite version and connection load."""
    if not output.get("available"):
        return
    connections = output.get("connections") or {}
    record_evidence_entry(
        evidence,
        source="get_mongodb_server_status",
        label="MongoDB Server Status",
        summary=(
            f"MongoDB {output.get('version', 'unknown')}, "
            f"{connections.get('current', 0)} connection(s) in use, "
            f"{connections.get('available', 0)} available"
        ),
    )


@tool(
    name="get_mongodb_server_status",
    description="Retrieve high-level MongoDB server status including connections, memory usage, and operation counters.",
    use_cases=[
        "Get a high-level view of MongoDB connections, memory, and operation counters",
        "Confirm MongoDB server-wide load before drilling into specific operations",
    ],
    source="mongodb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mongodb_is_available,
    injected_params=("connection_string",),
    extract_params=mongodb_extract_params,
    evidence_mapper=_map_get_mongodb_server_status,
)
def get_mongodb_server_status(
    connection_string: str,
    auth_source: str = "admin",
    tls: bool = True,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch server status metrics from a MongoDB instance."""
    config = MongoDBConfig(
        connection_string=connection_string,
        auth_source=auth_source,
        tls=tls,
    )
    return get_server_status(config)
