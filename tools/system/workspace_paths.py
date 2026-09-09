"""Keep a caller-supplied path inside the directory the shell is working in.

Read-only tools run without an approval gate, so a path they accept must not
reach outside the workspace: an absolute path, a ``..`` climb, or a symlink
used as the root would otherwise let a question about this project enumerate
somewhere else and report what it found.
"""

from __future__ import annotations

from pathlib import Path


class WorkspacePathError(ValueError):
    """The path resolves outside the working directory."""


def resolve_within_workspace(path: str | Path, *, workspace: Path | None = None) -> Path:
    """Return ``path`` resolved under ``workspace``, or raise.

    Resolution follows symlinks first, so a link pointing outside is rejected
    on where it lands rather than on how it is spelled.
    """
    root = (workspace or Path.cwd()).resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise WorkspacePathError(f"{path} is outside the working directory")
    return resolved


__all__ = ["WorkspacePathError", "resolve_within_workspace"]
