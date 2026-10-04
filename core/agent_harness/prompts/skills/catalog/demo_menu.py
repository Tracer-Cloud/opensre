"""Derive the onboarding entry menu and handoffs from each child's demo metadata."""

from __future__ import annotations

from dataclasses import replace

from config.constants.skills import (
    ONBOARDING_LEAF_CHOICES,
    ONBOARDING_MENU_TITLE,
    ONBOARDING_SKILL_NAME,
    OUTCOME_MENU_OPTIONS,
    SKIP_DEMO_OPTION,
)
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


def _outcome_menu(children: tuple[ActionSkill, ...]) -> bool:
    """True when the shipped onboarding children are present, so the outcome menu applies."""
    names = {skill.name for skill in children}
    return all(name in names for name, _label in ONBOARDING_LEAF_CHOICES)


def onboarding_entry_menu(skills: tuple[ActionSkill, ...]) -> SkillEntryMenu:
    """Build the master menu; raise ``ValueError`` if the child count is unrenderable.

    The shipped onboarding children use the outcome rows (analyze, automation,
    shell). Any other child set keeps each ``getting_started`` label plus Skip.
    """
    children = demo_skills(skills)
    labels = [skill.getting_started for skill in children if skill.getting_started]
    if not _MIN_DEMO_CHILDREN <= len(labels) <= _MAX_DEMO_CHILDREN:
        raise ValueError(
            f"needs {_MIN_DEMO_CHILDREN}-{_MAX_DEMO_CHILDREN} demo children, found {len(labels)}"
        )
    options = OUTCOME_MENU_OPTIONS if _outcome_menu(children) else (*labels, SKIP_DEMO_OPTION)
    return SkillEntryMenu(
        title=ONBOARDING_MENU_TITLE,
        options=options,
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
    """Render the leaf labels the model matches, with their canonical skill names.

    The automation group row is not a handoff: the shell resolves it to a leaf
    before the model sees an answer.
    """
    children = demo_skills(skills)
    if _outcome_menu(children):
        rows = [
            f'- "{label}": call `skill_view(name="{name}")`.'
            for name, label in ONBOARDING_LEAF_CHOICES
        ]
    else:
        rows = [
            f'- "{skill.getting_started}": call `skill_view(name="{skill.name}")`.'
            for skill in children
        ]
    rows.append(f'- "{SKIP_DEMO_OPTION}": finish onboarding.')
    return "\n\n## Current demo choices\n\n" + "\n".join(rows)
