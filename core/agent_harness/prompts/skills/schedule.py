"""Scheduling contract for recurring action-agent skills."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from core.agent_harness.prompts.skills.loader import (
    ActionSkill,
    list_action_skills,
    load_skill_body,
)

__all__ = (
    "ScheduledSkillResolution",
    "find_action_skill",
    "is_recurring_skill",
    "normalize_skill_name",
    "pin_recurring_skill",
    "resolve_scheduled_skill",
    "skill_revision",
    "validate_skill_inputs",
)


@dataclass(frozen=True, slots=True)
class ScheduledSkillResolution:
    """A pinned skill resolved for one scheduled tick."""

    skill: ActionSkill
    body: str
    revision: str

    @property
    def name(self) -> str:
        return self.skill.name


def normalize_skill_name(name: str) -> str:
    """Return the canonical kebab-case skill slug."""
    return name.strip().lower().replace("_", "-")


def find_action_skill(name: str) -> ActionSkill | None:
    """Return the discovered skill for ``name``, or ``None`` if unknown."""
    needle = normalize_skill_name(name)
    if not needle:
        return None
    return next((skill for skill in list_action_skills() if skill.name == needle), None)


def is_recurring_skill(name: str) -> bool:
    """True when ``name`` names a skill explicitly marked recurring."""
    skill = find_action_skill(name)
    return bool(skill is not None and (skill.recurring or "").strip())


def _hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def skill_revision(skill: ActionSkill) -> str:
    """Return a stable SHA-256 pin for the skill body the scheduler executes."""
    return _hash_body(load_skill_body(skill.name))


def pin_recurring_skill(name: str) -> tuple[str, str]:
    """Return ``(skill_name, revision)`` or raise if the skill cannot be scheduled."""
    skill = find_action_skill(name)
    slug = normalize_skill_name(name)
    if skill is None or not (skill.recurring or "").strip():
        raise RuntimeError(f"Skill {slug!r} is unknown or not marked recurring.")
    return skill.name, skill_revision(skill)


def resolve_scheduled_skill(name: str, pinned_revision: str) -> ScheduledSkillResolution:
    """Load ``name`` and fail when the skill is missing or revision drifts."""
    skill = find_action_skill(name)
    slug = normalize_skill_name(name)
    if skill is None:
        raise RuntimeError(f"Scheduled skill {slug!r} is not installed.")
    if not (skill.recurring or "").strip():
        raise RuntimeError(
            f"Scheduled skill {skill.name!r} is not marked recurring and cannot run unattended."
        )
    body = load_skill_body(skill.name)
    current = _hash_body(body)
    wanted = pinned_revision.strip()
    if not wanted:
        raise RuntimeError(f"Scheduled skill {skill.name!r} is missing a revision pin.")
    if current != wanted:
        raise RuntimeError(
            f"Scheduled skill {skill.name!r} changed since it was scheduled "
            f"(pinned {wanted[:12]}…, current {current[:12]}…). "
            "Remove and re-add the schedule to accept the new recipe."
        )
    return ScheduledSkillResolution(skill=skill, body=body, revision=current)


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
