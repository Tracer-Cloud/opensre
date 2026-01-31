"""Memory cache retrieval and lookup."""

from pathlib import Path

from app.agent.memory.io import list_memory_files, read_memory_file
from app.agent.memory.parser import parse_memory_sections


def find_latest_investigation(pipeline_name: str) -> Path | None:
    """
    Find the most recent memory file for a pipeline.

    Args:
        pipeline_name: Pipeline name to lookup

    Returns:
        Path to latest memory file, or None if not found
    """
    memory_files = list_memory_files(pipeline_name)
    return memory_files[0] if memory_files else None


def get_cached_investigation(pipeline_name: str) -> dict | None:
    """
    Get cached investigation results for direct reuse.

    Returns most recent high-quality investigation if available.

    Args:
        pipeline_name: Pipeline name to lookup

    Returns:
        Dict with cached results (action_sequence, root_cause_pattern, etc.)
        or None if no suitable cache found
    """
    latest_file = find_latest_investigation(pipeline_name)
    if not latest_file:
        return None

    try:
        content = read_memory_file(latest_file)
        cached = parse_memory_sections(content)

        if cached:
            print(f"[MEMORY] Loaded cache from {latest_file.name}")

        return cached if cached else None

    except Exception as e:
        print(f"[WARNING] Failed to parse memory file: {e}")
        return None
