"""Render the compact, cached skill index for action prompts."""

from __future__ import annotations

from functools import lru_cache

from config.constants.skills import SKILLS_HEADER
from core.agent_harness.prompts.skills.catalog.contracts import ActionSkill
from core.agent_harness.prompts.skills.catalog.registry import list_action_skills


def _index_line(skill: ActionSkill) -> str:
    recurring = " [recurring]" if skill.recurring else ""
    return f"- {skill.name} — {skill.description}{recurring}"


@lru_cache(maxsize=1)
def load_skills_index() -> str:
    """Return the compact SKILLS INDEX for the stable system prompt."""
    skills = list_action_skills()
    if not skills:
        return ""
    lines = [
        SKILLS_HEADER,
        "",
        "Compact catalog only — full skill bodies are NOT inlined here.",
        "Skill matches outrank a generic docs/how-to answer.",
        "Before answering, check this catalog for an action-shaped match",
        '(including "set up", "install", "onboard me", "demo", "audit", or "fix").',
        'For capability questions ("what can you do", "how can you help"),',
        "follow the getting-started instruction to answer first and offer /demo.",
        "When the user request matches a skill below, call skill_view(name) in",
        "this turn. Read its result before creating or revising its plan or",
        "calling its workflow tools. Request dependent update_plan calls in a",
        "later tool-call batch, after reading the skill's full instructions.",
        "",
    ]
    lines.extend(_index_line(skill) for skill in skills)
    return "".join(("\n".join(lines), "\n\n"))
