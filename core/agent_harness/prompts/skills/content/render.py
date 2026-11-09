"""Render one validated skill's full instructions, as ``skill_view`` returns them."""

from __future__ import annotations

from pathlib import Path

from config.constants.skill_prerequisites import (
    CONNECT_INTEGRATIONS_HEADING,
    prerequisite_section,
)
from config.constants.skill_success import success_section
from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.prompts.skills.catalog.contracts import ActionSkill
from core.agent_harness.prompts.skills.catalog.demo_menu import demo_handoffs
from core.agent_harness.prompts.skills.catalog.schema import parse_frontmatter
from core.agent_harness.prompts.skills.content.files import (
    append_report_template,
    append_skill_includes,
)


def render_skill_body(skill: ActionSkill, *, skills: tuple[ActionSkill, ...], root: Path) -> str:
    """Return the card body with includes, report template, and host-owned sections.

    ``skills`` is the catalog the card belongs to (for onboarding handoffs);
    ``root`` is the catalog root includes must stay inside. Raises ``OSError``,
    ``UnicodeError`` or ``SkillCardError`` when the card cannot be read.
    """
    _frontmatter, body = parse_frontmatter(skill.path.read_text(encoding="utf-8"))
    body = append_skill_includes(skill.path, body, skill.includes, root=root)
    body = append_report_template(skill.path, body, root=root)
    section = success_section(skill.name)
    if section and "## Success criteria" not in body:
        body = f"{body}\n\n{section}"
    if skill.name == ONBOARDING_SKILL_NAME:
        body += demo_handoffs(skills)
    prerequisite = prerequisite_section(skill.name)
    if prerequisite and CONNECT_INTEGRATIONS_HEADING not in body:
        body = f"{prerequisite}\n{body}"
    return body


__all__ = ["render_skill_body"]
