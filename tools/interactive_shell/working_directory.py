"""Working-directory helpers shared by local interactive-shell tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def session_working_directory(session: Any) -> str:
    """Return the session directory, falling back for structural test doubles."""
    value = getattr(session, "working_directory", None)
    if isinstance(value, str) and value.strip():
        return value
    return str(Path.cwd())


def resolve_working_directory(path: str, *, current: str) -> str:
    """Resolve and validate a typed working-directory path."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(current) / candidate
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise NotADirectoryError(f"not a directory: {resolved}")
    return str(resolved)


__all__ = ["resolve_working_directory", "session_working_directory"]
