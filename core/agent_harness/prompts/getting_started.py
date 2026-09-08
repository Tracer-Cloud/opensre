"""Stable getting-started choices for the interactive agent and first-visit picker.

Each selectable demo option is owned by an action skill via ``getting_started``
and ``demo_order`` frontmatter. The custom-answer row is added by the UI.
"""

from __future__ import annotations

from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.prompts.skills import getting_started_skills

GETTING_STARTED_CUSTOM = "Or type your own answer..."

GETTING_STARTED_OPTIONS: tuple[str, ...] = tuple(
    skill.getting_started or "" for skill in getting_started_skills()
)
GETTING_STARTED_MENU: tuple[str, ...] = (*GETTING_STARTED_OPTIONS, GETTING_STARTED_CUSTOM)


def load_getting_started_block() -> str:
    """Route capability and demo requests to the master skill that owns the menu."""
    return (
        "When the user asks what you can do, what you're capable of, how you can "
        "help, what tools you have, or for a demo / getting-started suggestion: "
        f'call skill_view(name="{ONBOARDING_SKILL_NAME}") and follow that master skill. '
        "It owns the Ask User menu and the references to child skills. "
        "Do not invent a separate getting-started menu. When an answer arrives, "
        "continue the active skill instead of reopening onboarding."
    )


__all__ = [
    "GETTING_STARTED_CUSTOM",
    "GETTING_STARTED_MENU",
    "GETTING_STARTED_OPTIONS",
    "getting_started_skills",
    "load_getting_started_block",
]
