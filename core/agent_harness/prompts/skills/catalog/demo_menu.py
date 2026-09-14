"""Derive onboarding choices and handoffs from each child's demo metadata."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from pydantic import ValidationError

from config.constants.skills import ONBOARDING_SKILL_NAME, SKIP_DEMO_OPTION
from core.agent_harness.prompts.skills.catalog.contracts import (
    ActionSkill,
    SkillCatalog,
    SkillToolCall,
)
from core.agent_harness.prompts.skills.catalog.schema import SkillEntryCall


def demo_skills(skills: tuple[ActionSkill, ...]) -> tuple[ActionSkill, ...]:
    """Return demo children in their validated menu order."""
    return tuple(
        sorted(
            (skill for skill in skills if skill.getting_started),
            key=lambda skill: (skill.demo_order or 0, skill.name),
        )
    )


def populate_demo_menu(skills: tuple[ActionSkill, ...]) -> SkillCatalog:
    """Fill and validate the master menu without excluding usable child workflows."""
    options = [skill.getting_started for skill in demo_skills(skills)]
    options.append(SKIP_DEMO_OPTION)
    valid: list[ActionSkill] = []
    diagnostics: list[str] = []
    for skill in skills:
        if skill.name == ONBOARDING_SKILL_NAME:
            call = skill.pre_execute[0]
            try:
                menu = SkillEntryCall.model_validate(
                    {"tool": call.tool, "args": {**call.args, "options": options}}
                )
            except ValidationError as exc:
                diagnostics.append(f"{skill.path}: generated demo menu: {exc}")
                continue
            skill = replace(
                skill, pre_execute=(SkillToolCall(menu.tool, MappingProxyType(menu.args)),)
            )
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
