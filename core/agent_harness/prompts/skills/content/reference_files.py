"""Read a workflow card's on-demand ``references/*.md`` files."""

from __future__ import annotations

import re
from pathlib import Path

from config.constants.skills import SKILL_FILENAME

REFERENCES_DIRNAME = "references"
REFERENCE_NAME_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


def reference_paths(skill_path: Path) -> tuple[Path, ...]:
    """Return the card's ``references/*.md`` files sorted by slug (packages only)."""
    if skill_path.name != SKILL_FILENAME:
        return ()
    references_dir = skill_path.parent / REFERENCES_DIRNAME
    if not references_dir.is_dir():
        return ()
    return tuple(sorted(path for path in references_dir.glob("*.md") if path.is_file()))


def read_skill_references(skill_path: Path) -> dict[str, str]:
    """Return ``{slug: text}`` for each readable reference; unreadable files are skipped."""
    references: dict[str, str] = {}
    for path in reference_paths(skill_path):
        try:
            references[path.stem] = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
    return references


__all__ = [
    "REFERENCES_DIRNAME",
    "REFERENCE_NAME_RE",
    "read_skill_references",
    "reference_paths",
]
