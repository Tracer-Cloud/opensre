"""MongoDB Replica Set Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.mongodb import (
    MongoDBConfig,
    get_rs_status,
    mongodb_extract_params,
    mongodb_is_available,
)


def _map_get_mongodb_replica_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite replica set member health, or that this server isn't in a replica set."""
    if not output.get("available"):
        return
    members = output.get("members") or []
    if not members:
        note = output.get("note")
        if note:
            record_evidence_entry(
                evidence,
                source="get_mongodb_replica_status",
                label="MongoDB Replica Set Status",
                summary=note,
            )
        return
    unhealthy = [m.get("name", "") for m in members if m.get("health") != 1]
    summary = f"{len(members)} member(s) in replica set '{output.get('set_name', '')}'"
    if unhealthy:
        summary += f", unhealthy: {', '.join(unhealthy)}"
    record_evidence_entry(
        evidence,
        source="get_mongodb_replica_status",
        label="MongoDB Replica Set Status",
        summary=summary,
    )


@tool(
    name="get_mongodb_replica_status",
    description="Retrieve replica set status, member health, and oplog lag for a MongoDB instance.",
    use_cases=[
        "Check MongoDB replica set member health or oplog lag",
        "Diagnose an unhealthy or lagging MongoDB secondary",
    ],
    source="mongodb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mongodb_is_available,
    injected_params=("connection_string",),
    extract_params=mongodb_extract_params,
    evidence_mapper=_map_get_mongodb_replica_status,
)
def get_mongodb_replica_status(
    connection_string: str,
    auth_source: str = "admin",
    tls: bool = True,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch status of all members in the MongoDB replica set."""
    config = MongoDBConfig(
        connection_string=connection_string,
        auth_source=auth_source,
        tls=tls,
    )
    return get_rs_status(config)
