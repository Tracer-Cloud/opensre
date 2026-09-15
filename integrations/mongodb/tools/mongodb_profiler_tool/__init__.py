"""MongoDB Profiler Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.mongodb import (
    MongoDBConfig,
    get_profiler_data,
    mongodb_database_is_available,
    mongodb_extract_params,
)


def _map_get_mongodb_profiler_data(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the slowest profiled query, or why profiling data is unavailable.

    ``get_profiler_data`` applies its ``.limit()`` in the Mongo query itself,
    so ``total_entries`` is always exactly ``len(entries)`` -- there is no
    separate unbounded count. Say "shown" rather than implying this is every
    matching entry in ``system.profile``, since a busy database can have more
    slow queries than the query's result cap.
    """
    if not output.get("available"):
        return
    entries = output.get("entries") or []
    if not entries:
        note = output.get("note")
        if note:
            record_evidence_entry(
                evidence,
                source="get_mongodb_profiler_data",
                label="MongoDB Profiler",
                summary=note,
            )
        return
    # Entries are pre-sorted by ts descending (most recent first); find the slowest.
    slowest = max(entries, key=lambda e: e.get("millis", 0))
    record_evidence_entry(
        evidence,
        source="get_mongodb_profiler_data",
        label="MongoDB Profiler",
        summary=(
            f"{output.get('total_entries', len(entries))} slow quer{'y' if len(entries) == 1 else 'ies'} shown "
            f"above {output.get('threshold_ms', 0)}ms, slowest {slowest.get('millis', 0)}ms"
        ),
    )


@tool(
    name="get_mongodb_profiler_data",
    description="Retrieve slow queries from the MongoDB database system.profile collection (requires profiling enabled).",
    use_cases=[
        "Review slow MongoDB queries recorded by the profiler when profiling is enabled",
    ],
    source="mongodb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mongodb_database_is_available,
    injected_params=("connection_string",),
    extract_params=mongodb_extract_params,
    evidence_mapper=_map_get_mongodb_profiler_data,
)
def get_mongodb_profiler_data(
    connection_string: str,
    database: str,
    threshold_ms: int = 100,
    auth_source: str = "admin",
    tls: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch recent slow query entries for a specific database."""
    config = MongoDBConfig(
        connection_string=connection_string,
        database=database,
        auth_source=auth_source,
        tls=tls,
    )
    return get_profiler_data(config, threshold_ms=threshold_ms, limit=limit)
