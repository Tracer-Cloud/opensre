"""MongoDB Collection Stats Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.mongodb import (
    MongoDBConfig,
    get_collection_stats,
    mongodb_database_is_available,
    mongodb_extract_params,
)


def _map_get_mongodb_collection_stats(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite document count and storage size for the collection."""
    if not output.get("available"):
        return
    size_mb = round((output.get("size_bytes") or 0) / (1024 * 1024), 2)
    record_evidence_entry(
        evidence,
        source="get_mongodb_collection_stats",
        label="MongoDB Collection Stats",
        summary=(
            f"'{output.get('ns', '')}': {output.get('count', 0)} document(s), {size_mb}MB, "
            f"{output.get('index_count', 0)} index(es)"
        ),
    )


@tool(
    name="get_mongodb_collection_stats",
    description="Retrieve document counts, size metrics, and index information for a specific MongoDB collection.",
    use_cases=[
        "Check document count, size, or indexes for one MongoDB collection",
    ],
    source="mongodb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mongodb_database_is_available,
    injected_params=("connection_string",),
    extract_params=mongodb_extract_params,
    evidence_mapper=_map_get_mongodb_collection_stats,
)
def get_mongodb_collection_stats(
    connection_string: str,
    database: str,
    collection: str,
    auth_source: str = "admin",
    tls: bool = True,
) -> dict[str, Any]:
    """Fetch collection-level metrics (e.g. document count, index size) for a specific collection."""
    config = MongoDBConfig(
        connection_string=connection_string,
        database=database,
        auth_source=auth_source,
        tls=tls,
    )
    return get_collection_stats(config, collection=collection)
