"""
Memory system for investigation agent (Openclaw session-memory pattern).

Stores and retrieves prior investigation knowledge to speed up RCA.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
        if line_stripped.startswith("#"):
            extracted.append(line_stripped)
        # Include bullet lists
        elif line_stripped.startswith("-") or line_stripped.startswith("*"):
            extracted.append(line_stripped)
        # Include lines with keywords
        elif any(kw in line_stripped.lower() for kw in keywords):
            extracted.append(line_stripped)

    # Join and truncate
    result = "\n".join(extracted)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... (truncated)"

    return result


def get_memory_context(
    pipeline_name: str | None = None,
    alert_id: str | None = None,
    seed_paths: list[str] | None = None,
) -> str:
    """
    Load memory context from seed MD files and prior investigations.

    Args:
        pipeline_name: Pipeline name to load specific memories for
        alert_id: Alert ID (not used for retrieval, just for context)
        seed_paths: Optional list of MD files to seed from

    Returns:
        Memory context string (empty if disabled or not found)
    """
    # Check if memory is enabled
    if not os.getenv("TRACER_MEMORY_ENABLED"):
        return ""

    memory_parts = []

    # 1. Seed from specified MD files
    if seed_paths:
        for seed_path in seed_paths:
            path = Path(seed_path)
            if path.exists() and path.suffix == ".md":
                try:
                    content = path.read_text()
                    extracted = _extract_memory_from_md(content, max_chars=1500)
                    if extracted:
                        memory_parts.append(f"## Seed: {path.name}\n{extracted}")
                except Exception as e:
                    print(f"[WARNING] Could not read seed file {seed_path}: {e}")

    # 2. Load prior investigations for this pipeline (newest first)
    if pipeline_name:
        memories_dir = _get_memories_dir()
        if memories_dir.exists():
            # Find memory files matching this pipeline
            pattern = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}-{re.escape(pipeline_name)}-.*\.md")
            memory_files = [
                f
                for f in memories_dir.glob("*.md")
                if pattern.match(f.name) and f.name != "IMPLEMENTATION_PLAN.md"
            ]
            # Sort by name (date) descending, take most recent 3
            memory_files.sort(reverse=True)
            for mem_file in memory_files[:3]:
                try:
                    content = mem_file.read_text()
                    memory_parts.append(f"## Prior Investigation: {mem_file.name}\n{content}")
                except Exception as e:
                    print(f"[WARNING] Could not read memory file {mem_file}: {e}")

    # 3. Optional: BOOT.md (Openclaw boot-md pattern)
    boot_md = Path(__file__).parent.parent.parent / "BOOT.md"
    if boot_md.exists():
        try:
            content = boot_md.read_text()
            extracted = _extract_memory_from_md(content, max_chars=500)
            if extracted:
                memory_parts.append(f"## BOOT Context\n{extracted}")
        except Exception:
            pass  # Silently skip if BOOT.md fails

    if not memory_parts:
        return ""

    return "\n\n".join(memory_parts)


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
