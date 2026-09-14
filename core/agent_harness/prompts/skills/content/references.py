"""List and load a workflow card's on-demand Markdown references."""

from __future__ import annotations

import re
from pathlib import Path

from config.constants.skills import SKILL_FILENAME
from core.agent_harness.prompts.skills.catalog.registry import find_action_skill

_REFERENCES_DIRNAME = "references"
_REFERENCE_NAME_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


def _skill_references(skill_path: Path) -> tuple[str, ...]:
    """Return sorted stems of the skill's bundled ``references/*.md`` files."""
    if skill_path.name != SKILL_FILENAME:
        return ()
    references_dir = skill_path.parent / _REFERENCES_DIRNAME
    if not references_dir.is_dir():
        return ()
    return tuple(sorted(path.stem for path in references_dir.glob("*.md") if path.is_file()))


def skill_reference_names(name: str) -> tuple[str, ...]:
    """Return sorted reference slugs, which remain separate from automatic includes."""
    skill = find_action_skill(name)
    return _skill_references(skill.path) if skill is not None else ()


def load_skill_reference(name: str, reference: str) -> str:
    """Read a reference by plain slug, or return empty for an unknown skill or reference."""
    slug = reference.strip().lower()
    if not _REFERENCE_NAME_RE.match(slug):
        return ""
    skill = find_action_skill(name)
    if skill is None or skill.path.name != SKILL_FILENAME:
        return ""
    reference_path = skill.path.parent / _REFERENCES_DIRNAME / f"{slug}.md"
    if not reference_path.is_file():
        return ""
    try:
        return reference_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
