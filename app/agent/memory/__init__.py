"""
Memory system for investigation agent (Openclaw session-memory pattern).

Public API that orchestrates focused modules:
- config: Feature flags and configuration
- io: File system operations
- parser: MD parsing and extraction
- cache: Cache lookup and retrieval
- formatter: Memory file formatting
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.memory.cache import get_cached_investigation
from app.agent.memory.config import get_quality_gate_threshold, is_memory_enabled
from app.agent.memory.formatter import format_memory_content, generate_memory_filename
from app.agent.memory.io import get_memories_dir, write_memory_file

# Re-export for backward compatibility
__all__ = ["get_memory_context", "write_memory", "write_service_map", "is_memory_enabled", "get_cached_investigation"]


def get_memory_context(
    pipeline_name: str | None = None,
    alert_id: str | None = None,  # noqa: ARG001
    seed_paths: list[str] | None = None,  # noqa: ARG001
) -> str:
    """
    Load memory context from prior investigations (minimal, targeted).

    Orchestrates: config check → cache retrieval → summary formatting

    Args:
        pipeline_name: Pipeline name to load specific memories for
        alert_id: Alert ID (not used for retrieval, kept for API compatibility)
        seed_paths: MD files to seed from (not used - caching is better)

    Returns:
        Short memory summary string (empty if disabled or not found)
    """
    # Check if memory is enabled
    if not is_memory_enabled():
        return ""

    # Use cached investigation instead of long context
    cached = get_cached_investigation(pipeline_name) if pipeline_name else None

    if not cached:
        return ""

    # Build minimal summary (< 500 chars)
    parts = []
    if cached.get("action_sequence"):
        actions_str = " → ".join(cached["action_sequence"][:5])
        parts.append(f"Prior successful path: {actions_str}")

    if cached.get("root_cause_pattern"):
        root_cause = cached["root_cause_pattern"][:200]
        parts.append(f"Prior root cause: {root_cause}")

    return "\n".join(parts) if parts else ""


def write_memory(
    pipeline_name: str,
    alert_id: str,
    root_cause: str,
    validity_score: float,
    action_sequence: list[str] | None = None,
    data_lineage: str | None = None,
    problem_pattern: str | None = None,
    rca_report: str | None = None,
) -> Path | None:
    """
    Write investigation memory to file (Openclaw session-memory pattern).

    Orchestrates: config check → quality gate → formatting → file write

    Only writes if TRACER_MEMORY_ENABLED=1 and quality gate passes (validity >= 70%).

    Args:
        pipeline_name: Pipeline name
        alert_id: Alert ID (first 8 chars used)
        root_cause: Root cause summary
        validity_score: Claim validity score
        action_sequence: Successful action sequence
        data_lineage: Data lineage nodes
        problem_pattern: Problem statement pattern
        rca_report: Full RCA report (slack_message) for complete context

    Returns:
        Path to written file, or None if not written
    """
    if not is_memory_enabled():
        return None

    validity_threshold = get_quality_gate_threshold()
    if validity_score < validity_threshold:
        print(
            f"[MEMORY] Not persisting (quality gate): validity={validity_score:.0%}"
        )
        return None

    timestamp = datetime.now(UTC)
    filename = generate_memory_filename(pipeline_name, alert_id, timestamp)
    filepath = get_memories_dir() / filename

    alert_id_short = alert_id[:8] if alert_id else "unknown"
    content = format_memory_content(
        timestamp=timestamp,
        pipeline_name=pipeline_name,
        alert_id_short=alert_id_short,
        validity_score=validity_score,
        problem_pattern=problem_pattern,
        action_sequence=action_sequence,
        root_cause=root_cause,
        data_lineage=data_lineage,
        rca_report=rca_report,
    )

    try:
        result_path = write_memory_file(filepath, content)
        print(f"[MEMORY] Persisted to {result_path.name}")
        return result_path
    except Exception as e:
        print(f"[WARNING] Failed to write memory: {e}")
        return None


def write_service_map(
    evidence: dict[str, Any],
    raw_alert: dict[str, Any],
    context: dict[str, Any],
    pipeline_name: str,
    alert_name: str,
) -> None:
    """Build service map from investigation evidence and persist it.

    Runs pipeline-to-pipeline edge inference after building the asset graph,
    then persists asynchronously (non-blocking when SERVICE_MAP_WRITE_ASYNC=True).

    Only runs when SERVICE_MAP_ENABLED is True.
    """
    from app.agent.memory.service_map import (
        build_service_map,
        infer_feeds_into_edges,
        persist_service_map,
    )
    from app.agent.memory.service_map.config import is_service_map_enabled

    if not is_service_map_enabled():
        return

    try:
        service_map = build_service_map(
            evidence=evidence,
            raw_alert=raw_alert,
            context=context,
            pipeline_name=pipeline_name,
            alert_name=alert_name,
        )

        # Infer pipeline-to-pipeline dependency edges and merge into the map
        pipeline_edges = infer_feeds_into_edges(service_map)
        if pipeline_edges:
            existing_by_key = {
                (e["from_asset"], e["to_asset"], e["type"]): i
                for i, e in enumerate(service_map["edges"])
            }
            for edge in pipeline_edges:
                key = (edge["from_asset"], edge["to_asset"], edge["type"])
                if key in existing_by_key:
                    service_map["edges"][existing_by_key[key]] = edge
                else:
                    service_map["edges"].append(edge)

        persist_service_map(service_map)
    except Exception as e:
        print(f"[WARNING] Failed to write service map: {e}")
