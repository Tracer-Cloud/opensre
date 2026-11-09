"""Digest the source files that make up one skill."""

from __future__ import annotations

import hashlib
from pathlib import Path

from core.agent_harness.prompts.skills.catalog.contracts import ActionSkill
from core.agent_harness.prompts.skills.content import files
from core.agent_harness.prompts.skills.content.reference_files import reference_paths

_SCRIPTS_DIRNAME = "scripts"


def skill_source_files(skill: ActionSkill, root: Path) -> tuple[Path, ...]:
    """Return the card, its references, scripts, report template and includes."""
    paths: set[Path] = {skill.path, *reference_paths(skill.path)}
    if skill.script_tools:
        scripts = skill.path.parent / _SCRIPTS_DIRNAME
        paths.update(path for path in scripts.glob("*.py") if path.is_file())
    template = files.report_template_path(skill.path)
    if template.is_file():
        paths.add(template)
    for ref in skill.includes:
        include = files.resolve_skill_include(skill.path, ref, root=root)
        if include is not None:
            paths.add(include)
    return tuple(sorted(paths))


def _relative(path: Path, root: Path) -> str:
    for candidate, anchor in ((path, root), (path.resolve(), root.resolve())):
        try:
            return candidate.relative_to(anchor).as_posix()
        except ValueError:
            continue
    return path.name


def skill_digest(skill: ActionSkill, root: Path) -> str:
    """Return a sha256 over each source file's root-relative path and bytes.

    Independent of where the root lives, so the same content bundled in the
    binary and delivered as a release digests identically.
    """
    entries = sorted(
        (_relative(path, root), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in skill_source_files(skill, root)
    )
    joined = "".join(f"{relative}\n{content}\n" for relative, content in entries)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


__all__ = ["skill_digest", "skill_source_files"]
