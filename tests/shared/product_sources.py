"""Enumerate shipped Python sources, skipping anything vendored into the tree.

A virtualenv created inside a product package makes every ``rglob("*.py")``
scanner read tens of thousands of third-party files. Those files are not ours,
they can carry syntax the running interpreter rejects, and a guard that trips on
one of them reports a product defect that does not exist. Scanners take their
file list from here so a stray environment cannot turn them red.

Real case: a ``config/.venv`` broke three separate guards at once — the psutil
confinement scan, the wheel-packaging marker check and the constant-condition
scan — one of them with a ``SyntaxError`` raised from inside a vendored SDK.
"""

from __future__ import annotations

from pathlib import Path

#: Directory names that never hold shipped source.
_VENDORED_DIRS = frozenset({"__pycache__", "node_modules", "site-packages"})


def is_vendored(relative_path: Path) -> bool:
    """True for caches, dot-directories (``.venv``, ``.git``) and third-party trees."""
    return any(part in _VENDORED_DIRS or part.startswith(".") for part in relative_path.parts)


def product_python_files(root: Path) -> list[Path]:
    """Sorted ``.py`` files under ``root`` that are actually ours.

    Returns empty for a missing directory so callers can skip a root that this
    checkout does not have.
    """
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*.py") if not is_vendored(path.relative_to(root)))
