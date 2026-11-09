"""Build an immutable catalog snapshot from one catalog root."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from types import MappingProxyType

from pydantic import ValidationError

from core.agent_harness.prompts.skills.catalog.reader import read_skill_catalog
from core.agent_harness.prompts.skills.catalog.schema import SkillCardError
from core.agent_harness.prompts.skills.content.index_render import render_skills_index
from core.agent_harness.prompts.skills.content.reference_files import read_skill_references
from core.agent_harness.prompts.skills.content.render import render_skill_body
from core.agent_harness.prompts.skills.snapshot.catalog_snapshot import (
    SkillCatalogSnapshot,
    SkillSource,
)
from core.agent_harness.prompts.skills.snapshot.digest import skill_digest

logger = logging.getLogger(__name__)


def build_snapshot(
    root: Path,
    *,
    source: SkillSource,
    release: str,
    release_seq: int | None = None,
    strict: bool = False,
) -> SkillCatalogSnapshot:
    """Validate, render and digest every card under ``root``.

    A card whose body cannot be rendered is excluded with a diagnostic, the
    same way discovery excludes invalid cards.
    """
    catalog = read_skill_catalog(root, strict=strict)
    diagnostics = list(catalog.diagnostics)
    bodies: dict[str, str] = {}
    references: dict[str, MappingProxyType[str, str]] = {}
    digests: dict[str, str] = {}
    kept = []
    for skill in catalog.skills:
        try:
            bodies[skill.name] = render_skill_body(skill, skills=catalog.skills, root=root)
            digests[skill.name] = skill_digest(skill, root)
        except (OSError, UnicodeError, SkillCardError, ValidationError) as exc:
            diagnostics.append(f"{skill.path}: {exc}")
            continue
        references[skill.name] = MappingProxyType(read_skill_references(skill.path))
        kept.append(skill)
    skills = tuple(kept)
    identity = "".join(f"{name}:{digests[name]}\n" for name in sorted(digests))
    snapshot_id = hashlib.sha256(f"{release}\n{root}\n{identity}".encode()).hexdigest()[:16]
    return SkillCatalogSnapshot(
        source=source,
        release=release,
        root=root,
        skills=skills,
        diagnostics=tuple(diagnostics),
        bodies=MappingProxyType(bodies),
        references=MappingProxyType(references),
        digests=MappingProxyType(digests),
        index=render_skills_index(skills),
        release_seq=release_seq,
        id=snapshot_id,
    )


def log_diagnostics(snapshot: SkillCatalogSnapshot) -> None:
    """Report cards excluded from ``snapshot`` without preventing startup."""
    for diagnostic in snapshot.diagnostics:
        logger.warning("Skipping invalid skill: %s", diagnostic)


__all__ = ["build_snapshot", "log_diagnostics"]
