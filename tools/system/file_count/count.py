"""Count the project files under a directory, skipping generated output.

``find -type f`` counts caches and build output, so "how many test files" came
back as 233 where 103 exist: the pattern ``test_*`` also matched compiled
``.pyc`` files inside ``__pycache__``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from tools.system.workspace_paths import (
    WorkspacePathError,
    resolve_within_workspace,
    workspace_relative,
)

#: Directories whose contents are generated, vendored, or version-control
#: bookkeeping. Counting them answers a question nobody asked.
GENERATED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)
#: Names listed back to the caller; the count itself is always complete.
MAX_LISTED_NAMES = 20


@dataclass(frozen=True)
class FileTally:
    """How many files under a directory matched, and a few of their names."""

    root: str
    pattern: str
    count: int
    names: tuple[str, ...]
    skipped_directories: int


class FileCountError(ValueError):
    """The directory cannot be counted."""


def count_matching_files(root: Path, pattern: str = "*") -> FileTally:
    """Count files under ``root`` whose name matches ``pattern``.

    ``root`` must resolve inside the working directory. Generated directories
    are pruned, symlinked directories are not followed (a link back up the tree
    would count forever), an unreadable subtree raises rather than being skipped,
    and the count covers the whole remaining tree — never a truncated sample.
    """
    try:
        root = resolve_within_workspace(root)
    except WorkspacePathError as exc:
        raise FileCountError(str(exc)) from exc
    if not root.exists():
        raise FileCountError(f"{root} does not exist")
    if not root.is_dir():
        raise FileCountError(f"{root} is not a directory")

    def _stop(error: OSError) -> None:
        # A silently skipped subtree would make an exact-looking count partial.
        raise FileCountError(f"cannot read {error.filename}: {type(error).__name__}")

    glob = pattern.strip() or "*"
    matched: list[str] = []
    count = 0
    skipped = 0
    for current, directories, files in os.walk(root, onerror=_stop, followlinks=False):
        keep = [name for name in directories if name not in GENERATED_DIRECTORIES]
        skipped += len(directories) - len(keep)
        directories[:] = keep
        for name in files:
            if not fnmatch(name, glob):
                continue
            count += 1
            if len(matched) < MAX_LISTED_NAMES:
                matched.append(workspace_relative(Path(current, name)))
    return FileTally(
        root=workspace_relative(root),
        pattern=glob,
        count=count,
        names=tuple(matched),
        skipped_directories=skipped,
    )


__all__ = [
    "GENERATED_DIRECTORIES",
    "MAX_LISTED_NAMES",
    "FileCountError",
    "FileTally",
    "count_matching_files",
]
