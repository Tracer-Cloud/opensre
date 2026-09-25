"""MongoDB Current Ops Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.mongodb import (
    MongoDBConfig,
    get_current_ops,
    mongodb_extract_params,
    mongodb_is_available,
)


def _map_get_mongodb_current_ops(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the count of long-running operations above the threshold."""
    if not output.get("available"):
        return
    operations = output.get("operations") or []
    if not operations:
        return
    longest = max((op.get("secs_running", 0) for op in operations), default=0)
    record_evidence_entry(
        evidence,
        source="get_mongodb_current_ops",
        label="MongoDB Current Ops",
        summary=(
            f"{output.get('total_ops', len(operations))} op(s) over "
            f"{output.get('threshold_ms', 0)}ms, longest running {longest}s"
        ),
    )


@tool(
    name="get_mongodb_current_ops",
    description="Retrieve currently executing MongoDB operations above a specific duration threshold.",
    use_cases=[
        "Find MongoDB operations running longer than a threshold right now",
        "Find a MongoDB operation that is long-running or waiting on a lock",
    ],
    source="mongodb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mongodb_is_available,
    injected_params=("connection_string",),
    extract_params=mongodb_extract_params,
    evidence_mapper=_map_get_mongodb_current_ops,
)
def get_mongodb_current_ops(
    connection_string: str,
    threshold_ms: int = 1000,
    auth_source: str = "admin",
    tls: bool = True,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch currently running operations above the threshold (default 1000ms)."""
    config = MongoDBConfig(
        connection_string=connection_string,
        auth_source=auth_source,
        tls=tls,
    )
    return get_current_ops(config, threshold_ms=threshold_ms)
