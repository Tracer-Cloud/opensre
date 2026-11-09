"""Scheduling contract for recurring action-agent skills.

A schedule pins ``v2:<major>:<digest>``: the card's major version and a digest
of its source files. Ticks follow edits within the same major version (they run
read-only, and a minor bump is by contract non-breaking) and record the new pin;
a major bump stops the schedule until the user re-adds it. A pin written before
this format (a hash of the rendered body) is re-pinned only while that body is
unchanged; it carries no version, so any change needs the user to re-add it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from core.agent_harness.prompts.skills.catalog.contracts import ActionSkill
from core.agent_harness.prompts.skills.catalog.naming import normalize_skill_name
from core.agent_harness.prompts.skills.snapshot.active_catalog import active_skill_catalog
from core.agent_harness.prompts.skills.snapshot.catalog_snapshot import SkillCatalogSnapshot

__all__ = (
    "ScheduledSkillResolution",
    "is_recurring_skill",
    "pin_recurring_skill",
    "resolve_scheduled_skill",
    "skill_revision",
    "validate_skill_inputs",
)

_PIN_VERSION = "v2"
_LEGACY_PIN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ScheduledSkillResolution:
    """A pinned skill resolved for one scheduled tick."""

    skill: ActionSkill
    body: str
    revision: str
    #: The pin the schedule carried before this tick (differs when the skill changed).
    previous_revision: str = ""

    @property
    def name(self) -> str:
        return self.skill.name

    @property
    def repinned(self) -> bool:
        """True when the tick followed an edit and the schedule should store ``revision``."""
        return bool(self.previous_revision) and self.previous_revision != self.revision


def _major(version: str) -> str:
    return version.split(".", 1)[0].strip() or "0"


def _revision(skill: ActionSkill, snapshot: SkillCatalogSnapshot) -> str:
    return f"{_PIN_VERSION}:{_major(skill.version)}:{snapshot.digest(skill.name)}"


def is_recurring_skill(name: str) -> bool:
    """True when ``name`` names a skill explicitly marked recurring."""
    skill = active_skill_catalog().current().find(name)
    return bool(skill is not None and skill.recurring)


def skill_revision(skill: ActionSkill) -> str:
    """Return the schedule pin for ``skill`` in the active catalog."""
    return _revision(skill, active_skill_catalog().current())


def pin_recurring_skill(name: str) -> tuple[str, str]:
    """Return ``(skill_name, revision)`` or raise if the skill cannot be scheduled."""
    snapshot = active_skill_catalog().current()
    skill = snapshot.find(name)
    slug = normalize_skill_name(name)
    if skill is None or not skill.recurring:
        raise RuntimeError(f"Skill {slug!r} is unknown or not marked recurring.")
    return skill.name, _revision(skill, snapshot)


def resolve_scheduled_skill(name: str, pinned_revision: str) -> ScheduledSkillResolution:
    """Load ``name`` for a tick; fail when it is missing or its major version moved."""
    snapshot = active_skill_catalog().current()
    skill = snapshot.find(name)
    slug = normalize_skill_name(name)
    if skill is None:
        raise RuntimeError(f"Scheduled skill {slug!r} is not installed.")
    if not skill.recurring:
        raise RuntimeError(
            f"Scheduled skill {skill.name!r} is not marked recurring and cannot run unattended."
        )
    wanted = pinned_revision.strip()
    if not wanted:
        raise RuntimeError(f"Scheduled skill {skill.name!r} is missing a revision pin.")
    current = _revision(skill, snapshot)
    body = snapshot.body(skill.name)
    if _LEGACY_PIN.match(wanted):
        if wanted != hashlib.sha256(body.encode("utf-8")).hexdigest():
            raise RuntimeError(
                f"Scheduled skill {skill.name!r} changed since it was scheduled. "
                "Remove and re-add the schedule to accept the new recipe."
            )
    elif wanted != current:
        parts = wanted.split(":")
        pinned_major = parts[1] if len(parts) == 3 and parts[0] == _PIN_VERSION else ""
        if pinned_major != _major(skill.version):
            raise RuntimeError(
                f"Scheduled skill {skill.name!r} changed since it was scheduled "
                f"(major version {pinned_major or '?'} -> {_major(skill.version)}). "
                "Remove and re-add the schedule to accept the new recipe."
            )
    return ScheduledSkillResolution(
        skill=skill,
        body=body,
        revision=current,
        previous_revision=wanted,
    )


def validate_skill_inputs(raw: Mapping[str, object] | None) -> dict[str, str]:
    """Return string-only skill inputs or raise ``ValueError``."""
    if not raw:
        return {}
    validated: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError("skill input keys must be strings.")
        name = key.strip()
        if not name:
            raise ValueError("skill input keys must be non-empty strings.")
        if not isinstance(value, str):
            raise ValueError(f"skill input {name!r} must be a string.")
        validated[name] = value.strip()
    return validated
