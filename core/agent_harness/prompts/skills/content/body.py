"""Load current validated skill instructions on demand."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.prompts.skills.catalog.demo_menu import demo_handoffs
from core.agent_harness.prompts.skills.catalog.registry import (
    find_action_skill,
    list_action_skills,
    validate_skill_file,
)
from core.agent_harness.prompts.skills.catalog.schema import SkillCardError, parse_frontmatter
from core.agent_harness.prompts.skills.content.files import (
    append_report_template,
    append_skill_includes,
)

logger = logging.getLogger(__name__)


def load_skill_body(name: str) -> str:
    """Return current instructions with includes and a report template, or empty if invalid."""
    skill = find_action_skill(name)
    if skill is None:
        return ""
    try:
        current = validate_skill_file(skill.path)
        _frontmatter, body = parse_frontmatter(skill.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SkillCardError, ValidationError) as exc:
        logger.warning("Skipping invalid skill %s: %s", skill.path, exc)
        return ""
    body = append_skill_includes(skill.path, body, current.includes)
    body = append_report_template(skill.path, body)
    if skill.name == ONBOARDING_SKILL_NAME:
        body += demo_handoffs(list_action_skills())
    return body
