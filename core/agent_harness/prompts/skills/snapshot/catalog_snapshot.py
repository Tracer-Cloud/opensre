"""One immutable, fully rendered skill catalog."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from core.agent_harness.prompts.skills.catalog.contracts import ActionSkill
from core.agent_harness.prompts.skills.catalog.demo_menu import demo_skills
from core.agent_harness.prompts.skills.catalog.naming import normalize_skill_name


class SkillSource(StrEnum):
    """Where a snapshot's cards came from."""

    BUNDLED = "bundled"
    OVERRIDE = "override"
    REMOTE = "remote"


@dataclass(frozen=True)
class SkillCatalogSnapshot:
    """Validated cards plus every text the runtime serves from them.

    Built once from a catalog root and never re-read: bodies, references and the
    index are held in memory, so a turn sees one consistent catalog even while a
    newer release is being activated. ``root`` stays on disk only for helper
    scripts, which must run from real files.
    """

    source: SkillSource
    #: ``bundled:<binary version>``, ``override:<digest>`` or ``remote:<seq>``.
    release: str
    root: Path
    skills: tuple[ActionSkill, ...]
    diagnostics: tuple[str, ...]
    bodies: Mapping[str, str]
    references: Mapping[str, Mapping[str, str]]
    #: sha256 of each skill's source files, keyed by skill name.
    digests: Mapping[str, str]
    index: str
    #: Registry release sequence for ``REMOTE`` snapshots, else ``None``.
    release_seq: int | None = None
    #: Content-and-location identity for caches (script paths are absolute).
    id: str = ""
    _by_name: Mapping[str, ActionSkill] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_by_name", MappingProxyType({skill.name: skill for skill in self.skills})
        )

    def find(self, name: str) -> ActionSkill | None:
        """Return the skill for ``name`` (legacy slugs map forward), or ``None``."""
        needle = normalize_skill_name(name)
        return self._by_name.get(needle) if needle else None

    def body(self, name: str) -> str:
        """Return the rendered instructions for ``name``, or empty when unknown."""
        skill = self.find(name)
        return self.bodies.get(skill.name, "") if skill is not None else ""

    def reference_names(self, name: str) -> tuple[str, ...]:
        """Return the sorted reference slugs of ``name``."""
        skill = self.find(name)
        if skill is None:
            return ()
        return tuple(self.references.get(skill.name, {}))

    def reference(self, name: str, slug: str) -> str:
        """Return one reference's text, or empty for an unknown skill or slug."""
        skill = self.find(name)
        if skill is None:
            return ""
        return self.references.get(skill.name, {}).get(slug, "")

    def getting_started(self) -> tuple[ActionSkill, ...]:
        """Return the selectable demos in menu order."""
        return demo_skills(self.skills)

    def digest(self, name: str) -> str:
        """Return the source digest of ``name``, or empty when unknown."""
        skill = self.find(name)
        return self.digests.get(skill.name, "") if skill is not None else ""


__all__ = ["SkillCatalogSnapshot", "SkillSource"]
