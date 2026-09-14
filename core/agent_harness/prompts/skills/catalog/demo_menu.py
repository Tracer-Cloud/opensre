"""Derive the onboarding entry menu and handoffs from each child's demo metadata."""

from __future__ import annotations

from dataclasses import replace

from config.constants.skills import ONBOARDING_MENU_TITLE, ONBOARDING_SKILL_NAME, SKIP_DEMO_OPTION
from core.agent_harness.prompts.skills.catalog.contracts import (
    ActionSkill,
    SkillCatalog,
    SkillEntryMenu,
)

# The picker renders two to eight rows; one row is always the shell's Skip option.
_MIN_DEMO_CHILDREN = 1
_MAX_DEMO_CHILDREN = 7


def demo_skills(skills: tuple[ActionSkill, ...]) -> tuple[ActionSkill, ...]:
    """Return demo children in their validated menu order."""
    return tuple(
        sorted(
            (skill for skill in skills if skill.getting_started),
            key=lambda skill: (skill.demo_order or 0, skill.name),
        )
    )


def onboarding_entry_menu(skills: tuple[ActionSkill, ...]) -> SkillEntryMenu:
    """Build the master menu from the children's labels; raise ``ValueError`` if unrenderable."""
    labels = [skill.getting_started for skill in demo_skills(skills) if skill.getting_started]
    if not _MIN_DEMO_CHILDREN <= len(labels) <= _MAX_DEMO_CHILDREN:
        raise ValueError(
            f"needs {_MIN_DEMO_CHILDREN}-{_MAX_DEMO_CHILDREN} demo children, found {len(labels)}"
        )
    return SkillEntryMenu(
        title=ONBOARDING_MENU_TITLE,
        options=(*labels, SKIP_DEMO_OPTION),
        allow_custom=False,
    )


def populate_demo_menu(skills: tuple[ActionSkill, ...]) -> SkillCatalog:
    """Attach the generated master menu without excluding usable child workflows."""
    valid: list[ActionSkill] = []
    diagnostics: list[str] = []
    for skill in skills:
        if skill.name == ONBOARDING_SKILL_NAME:
            try:
                menu = onboarding_entry_menu(skills)
            except ValueError as exc:
                diagnostics.append(f"{skill.path}: generated demo menu: {exc}")
                continue
            skill = replace(skill, entry_menu=menu)
        valid.append(skill)
    return SkillCatalog(tuple(valid), tuple(diagnostics))


def demo_handoffs(skills: tuple[ActionSkill, ...]) -> str:
    """Render the selectable labels with their canonical skill names."""
    rows = [
        f'- "{skill.getting_started}": call `skill_view(name="{skill.name}")`.'
        for skill in demo_skills(skills)
    ]
    rows.append(f'- "{SKIP_DEMO_OPTION}": finish onboarding.')
    return "\n\n## Current demo choices\n\n" + "\n".join(rows)
