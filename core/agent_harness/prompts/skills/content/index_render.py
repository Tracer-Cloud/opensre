"""Render the compact skill index text from a catalog."""

from __future__ import annotations

from config.constants.skills import SKILLS_HEADER
from core.agent_harness.prompts.skills.catalog.contracts import ActionSkill


def _index_line(skill: ActionSkill) -> str:
    recurring = " [recurring]" if skill.recurring else ""
    return f"- {skill.name} — {skill.description}{recurring}"


def render_skills_index(skills: tuple[ActionSkill, ...]) -> str:
    """Return the compact SKILLS INDEX for ``skills`` (empty when there are none)."""
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


def render_skills_prompt_index(skills: tuple[ActionSkill, ...]) -> str:
    """Return the small workflow-discovery block sent on every agent turn."""
    if not skills:
        return ""
    names = ", ".join(
        f"{skill.name}{' [recurring]' if skill.recurring else ''}" for skill in skills
    )
    return (
        f"{SKILLS_HEADER}\n\n"
        "Workflow details load on demand.\n"
        "Available workflow names: "
        f"{names}.\n"
        "For a likely multi-step workflow, call skill_view with query set to the "
        "user's desired outcome. Then load the best exact name and follow its returned "
        "instructions. For a known exact name, call skill_view(name) directly. Do not "
        "load a workflow for a simple factual answer. For capability questions, answer "
        "first and offer /demo. An onboarding router delegates the live plan to its child.\n\n"
    )


__all__ = ["render_skills_index", "render_skills_prompt_index"]
