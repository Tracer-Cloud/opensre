"""Memory file system operations."""

import re
from pathlib import Path


def get_memories_dir() -> Path:
    """Get the memories directory path."""
    return Path(__file__).parent.parent.parent / "memories"


def list_memory_files(pipeline_name: str) -> list[Path]:
    """
    List memory files for a specific pipeline (newest first).

    Args:
        pipeline_name: Pipeline name to filter by

    Returns:
        List of Path objects, sorted by date (newest first)
    """
    memories_dir = get_memories_dir()
    if not memories_dir.exists():
        return []

    # Pattern: YYYY-MM-DD-<pipeline_name>-<alert_id>.md
    pattern = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}-{re.escape(pipeline_name)}-.*\.md")
    memory_files = [
        f
        for f in memories_dir.glob("*.md")
        if pattern.match(f.name) and f.name not in ("IMPLEMENTATION_PLAN.md", "FINDINGS.md")
    ]

    # Sort by filename (date) descending
    memory_files.sort(reverse=True)
    return memory_files


def read_memory_file(filepath: Path) -> str:
    """
    Read content from a memory file.

    Args:
        filepath: Path to memory file

    Returns:
        File content as string

    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If file can't be read
    """
    return filepath.read_text()


def write_memory_file(filepath: Path, content: str) -> Path:
    """
    Write content to a memory file.

    Creates parent directories if needed.

    Args:
        filepath: Path to write to
        content: Content to write

    Returns:
        Path to written file

    Raises:
        IOError: If file can't be written
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    return filepath
