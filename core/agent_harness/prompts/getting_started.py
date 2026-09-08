"""Stable getting-started choices for the interactive agent and first-visit picker.

Each selectable demo option is owned by an action skill via ``getting_started``
and ``demo_order`` frontmatter. Custom (D) is not a skill.
"""

from __future__ import annotations

from core.agent_harness.prompts.skills import getting_started_skills

GETTING_STARTED_CUSTOM = "Or type your own answer..."

GETTING_STARTED_OPTIONS: tuple[str, ...] = tuple(
    skill.getting_started or "" for skill in getting_started_skills()
)
GETTING_STARTED_MENU: tuple[str, ...] = (*GETTING_STARTED_OPTIONS, GETTING_STARTED_CUSTOM)

_GETTING_STARTED_RULE = (
    "When the user asks what you can do, what you're capable of, how you can "
    "help, what tools you have, or for a demo / getting-started suggestion: "
    "call `ask_user_choice` with ONLY the getting-started options below, in "
    "the order shown. Use each option verbatim; do not rephrase, add, remove, "
    "or reorder options, and do not put the skill name in the menu. The "
    "interactive surface always appends "
    f"`{GETTING_STARTED_CUSTOM}` as the last row, so do not include a "
    "custom-answer option. Do not list platform features, slash commands, "
    "AGENTS.md capabilities, or add a Want-me-to closer that invents another "
    "action. When the user picks an option, call skill_view with that "
    "option's attached skill in THIS turn before acting."
)


def load_getting_started_block() -> str:
    """Return the agent rule, exact selectable starter prompts, and skill map."""
    skills = getting_started_skills()
    lines = [_GETTING_STARTED_RULE, "", "Options:"]
    lines.extend(f"- {skill.getting_started}" for skill in skills)
    lines.extend(("", "Attached skills (skill_view after the pick; not menu text):"))
    lines.extend(f"- {skill.getting_started} → `{skill.name}`" for skill in skills)
    return "\n".join(lines)


__all__ = [
    "GETTING_STARTED_CUSTOM",
    "GETTING_STARTED_MENU",
    "GETTING_STARTED_OPTIONS",
    "getting_started_skills",
    "load_getting_started_block",
]
