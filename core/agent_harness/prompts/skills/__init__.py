"""Progressive action-agent skills: thin harness index, fat markdown bodies on demand."""

from __future__ import annotations

from core.agent_harness.prompts.skills.loader import (
    SKILLS_HEADER,
    ActionSkill,
    getting_started_skills,
    list_action_skills,
    load_skill_body,
    load_skills_block,
    load_skills_index,
    skills_dir,
)

__all__ = [
    "ActionSkill",
    "SKILLS_HEADER",
    "getting_started_skills",
    "list_action_skills",
    "load_skill_body",
    "load_skills_block",
    "load_skills_index",
    "skills_dir",
]
