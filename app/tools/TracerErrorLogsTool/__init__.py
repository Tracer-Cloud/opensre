"""Tracer runtime log tool."""

from __future__ import annotations

from typing import Any

from app.services.tracer_client import get_tracer_web_client
from app.tools.tool_decorator import tool
from app.tools.utils.log_compaction import build_error_taxonomy, deduplicate_logs


def _error_logs_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("tracer_web", {}).get("trace_id"))


def _error_logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return {
        "trace_id": sources.get("tracer_web", {}).get("trace_id"),
        "size": 500,
        "error_only": True,
    }


@tool(
    name="get_error_logs",
    source="tracer_web",
    description="Get logs from OpenSearch, optionally filtered for errors.",
    use_cases=[
        "Proving error pattern hypothesis",
        "Finding root cause error messages",
        "Understanding failure timeline",
    ],
    requires=["trace_id"],
    input_schema={
        "type": "object",
        "properties": {
            "trace_id": {"type": "string"},
            "size": {"type": "integer", "default": 500},
            "error_only": {"type": "boolean", "default": True},
        },
        "required": ["trace_id"],
    },
    is_available=_error_logs_available,
    extract_params=_error_logs_extract_params,
    surfaces=("investigation", "chat"),
)
def get_error_logs(trace_id: str, size: int = 500, error_only: bool = True) -> dict[str, Any]:
    """Get logs from OpenSearch, optionally filtered for errors.

    Logs are deduplicated and grouped by message pattern so that bursts of
    identical errors (e.g. 48 repeated timeouts) collapse into a single entry
    with a ``count``, freeing slots for distinct errors the LLM would otherwise
    never see.  An ``error_taxonomy`` summary is also included so the LLM
    receives a birds-eye view of all error types across the full fetched set.
    """
    if not trace_id:
        return {"error": "trace_id is required"}

    client = get_tracer_web_client()
    logs_data = client.get_logs(run_id=trace_id, size=size)

    if not isinstance(logs_data, dict):
        logs_data = {"data": [], "success": False}
    if "data" not in logs_data:
        logs_data = {"data": logs_data if isinstance(logs_data, list) else [], "success": True}

    log_list = logs_data.get("data", [])

    # Normalise each raw log into a consistent shape (message capped at 500 chars)
    normalised: list[dict] = [
        {
            "message": log.get("message", "")[:500],
            "log_level": log.get("log_level"),
            "timestamp": log.get("timestamp"),
        }
        for log in log_list
    ]

    if error_only:
        filtered = [
            log for log in normalised
            if "error" in str(log.get("log_level", "")).lower()
            or "fail" in str(log.get("message", "")).lower()
        ]
    else:
        filtered = normalised

    # Phase 1: deduplicate + count-group (cap preserved for downstream compat)
    max_output = 50 if error_only else 200
    compacted = deduplicate_logs(filtered, max_output=max_output)

    # Phase 2: structured error taxonomy across the *full* filtered set
    error_taxonomy = build_error_taxonomy(filtered)

    return {
        "logs": compacted,
        "total_logs": len(log_list),
        "filtered_count": len(filtered),
        "compacted_count": len(compacted),
        "error_only": error_only,
        "error_taxonomy": error_taxonomy,
        "source": "opensearch/logs API",
    }
