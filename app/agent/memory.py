"""
Memory system for investigation agent (Openclaw session-memory pattern).

Stores and retrieves prior investigation knowledge to speed up RCA.
"""

import os
import re
from datetime import UTC, datetime
from pathlib import Path


def _get_memories_dir() -> Path:
    """Get the memories directory path."""
    return Path(__file__).parent.parent / "memories"


def _extract_memory_from_md(md_content: str, max_chars: int = 2000) -> str:
    """
    Extract key patterns from markdown files.

    Simple heuristic extraction:
    - Headings (## lines)
    - Bullet lists (- lines)
    - Lines containing key investigation terms
    """
    lines = md_content.split("\n")
    extracted = []

    # Keywords that indicate useful investigation patterns
    keywords = [
        "schema",
        "audit",
        "external api",
        "lambda",
        "s3",
        "root cause",
        "failure",
        "missing field",
        "validation",
        "prefect",
        "ecs",
    ]

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Include headings
        if line_stripped.startswith("#") or line_stripped.startswith("-") or line_stripped.startswith("*") or any(kw in line_stripped.lower() for kw in keywords):
            extracted.append(line_stripped)

    # Join and truncate
    result = "\n".join(extracted)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... (truncated)"

    return result


def get_cached_investigation(pipeline_name: str) -> dict | None:
    """
    Get cached investigation results for direct reuse (skip LLM calls).

    Returns most recent high-quality investigation if available.

    Args:
        pipeline_name: Pipeline name to lookup

    Returns:
        Dict with cached results (action_sequence, root_cause_pattern, problem_pattern)
        or None if no suitable cache found
    """
    if not os.getenv("TRACER_MEMORY_ENABLED") or not pipeline_name:
        return None

    memories_dir = _get_memories_dir()
    if not memories_dir.exists():
        return None

    # Find recent memory files for this pipeline
    pattern = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}-{re.escape(pipeline_name)}-.*\.md")
    memory_files = [
        f
        for f in memories_dir.glob("*.md")
        if pattern.match(f.name) and f.name != "IMPLEMENTATION_PLAN.md"
    ]

    if not memory_files:
        return None

    # Get most recent
    memory_files.sort(reverse=True)
    latest_file = memory_files[0]

    try:
        content = latest_file.read_text()

        # Extract key sections
        cached = {}

        # Extract action sequence
        if "## Investigation Path" in content:
            path_section = content.split("## Investigation Path")[1].split("##")[0]
            actions = [line.split(". ", 1)[1] for line in path_section.strip().split("\n") if ". " in line]
            cached["action_sequence"] = actions

        # Extract root cause pattern
        if "## Root Cause" in content:
            root_cause = content.split("## Root Cause")[1].split("##")[0].strip()
            cached["root_cause_pattern"] = root_cause

        # Extract problem pattern
        if "## Problem Pattern" in content:
            problem = content.split("## Problem Pattern")[1].split("##")[0].strip()
            cached["problem_pattern"] = problem

        # Extract data lineage
        if "## Data Lineage" in content:
            lineage = content.split("## Data Lineage")[1].split("##")[0].strip()
            cached["data_lineage"] = lineage

        print(f"[MEMORY] Loaded cache from {latest_file.name}")
        return cached if cached else None

    except Exception as e:
        print(f"[WARNING] Failed to parse memory file: {e}")
        return None


def get_memory_context(
    pipeline_name: str | None = None,
    alert_id: str | None = None,  # noqa: ARG001
    seed_paths: list[str] | None = None,  # noqa: ARG001
) -> str:
    """
    Load memory context from prior investigations (minimal, targeted).

    Args:
        pipeline_name: Pipeline name to load specific memories for
        alert_id: Alert ID (not used for retrieval, kept for API compatibility)
        seed_paths: MD files to seed from (not used - caching is better)

    Returns:
        Short memory summary string (empty if disabled or not found)
    """
    # Check if memory is enabled
    if not os.getenv("TRACER_MEMORY_ENABLED"):
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
    confidence: float,
    validity_score: float,
    action_sequence: list[str] | None = None,
    data_lineage: str | None = None,
    problem_pattern: str | None = None,
) -> Path | None:
    """
    Write investigation memory to file (Openclaw session-memory pattern).

    Only writes if TRACER_MEMORY_ENABLED=1 and quality gate passes (confidence + validity >70%).

    Args:
        pipeline_name: Pipeline name
        alert_id: Alert ID (first 8 chars used)
        root_cause: Root cause summary
        confidence: Investigation confidence
        validity_score: Claim validity score
        action_sequence: Successful action sequence
        data_lineage: Data lineage nodes
        problem_pattern: Problem statement pattern

    Returns:
        Path to written file, or None if not written
    """
    # Check if memory is enabled
    if not os.getenv("TRACER_MEMORY_ENABLED"):
        return None

    # Quality gate: only persist high-quality investigations
    if confidence < 0.7 or validity_score < 0.7:
        print(
            f"[MEMORY] Not persisting (quality gate): confidence={confidence:.0%}, validity={validity_score:.0%}"
        )
        return None

    # Prepare memory directory
    memories_dir = _get_memories_dir()
    memories_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename (Openclaw pattern: deterministic, no LLM)
    timestamp = datetime.now(UTC)
    date_str = timestamp.strftime("%Y-%m-%d")
    alert_id_short = alert_id[:8] if alert_id else "unknown"
    filename = f"{date_str}-{pipeline_name}-{alert_id_short}.md"
    filepath = memories_dir / filename

    # Build memory content
    content_parts = [
        f"# Session: {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- **Pipeline**: {pipeline_name}",
        f"- **Alert ID**: {alert_id_short}",
        f"- **Confidence**: {confidence:.0%}",
        f"- **Validity**: {validity_score:.0%}",
        "",
    ]

    if problem_pattern:
        content_parts.extend(["## Problem Pattern", problem_pattern, ""])

    if action_sequence:
        content_parts.extend(
            ["## Investigation Path", "\n".join(f"{i}. {action}" for i, action in enumerate(action_sequence, 1)), ""]
        )

    content_parts.extend(["## Root Cause", root_cause, ""])

    if data_lineage:
        content_parts.extend(["## Data Lineage", data_lineage, ""])

    # Write file
    try:
        filepath.write_text("\n".join(content_parts))
        print(f"[MEMORY] Persisted to {filepath.name}")
        return filepath
    except Exception as e:
        print(f"[WARNING] Failed to write memory: {e}")
        return None


def is_memory_enabled() -> bool:
    """Check if memory system is enabled via env var."""
    return bool(os.getenv("TRACER_MEMORY_ENABLED"))
