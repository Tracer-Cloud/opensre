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
    """Separate capability answers, direct specialist requests, and guided onboarding."""
    return (
        "When the user asks what you can do, what you're capable of, how you can "
        "help, or what tools you have, answer from the available capabilities and "
        "offer /demo. When the user names a demo or requests a specialist's work, "
        "load that specialist directly and carry the original request forward. "
        "For an ambiguous CI request, clarify the desired outcome once. "
        "For an explicit demo or onboarding request that needs path selection, "
        f'call skill_view(name="{ONBOARDING_SKILL_NAME}") and follow that master skill. '
        "It owns the Ask User menu and the references to child skills. "
        "Do not invent a separate getting-started menu. When an answer arrives, "
        "continue the active skill instead of reopening onboarding. "
        "If the picker for onboarding or an ambiguous CI request is unavailable, "
        "explain the limitation, invite a "
        "direct task request, and stop onboarding without a replacement text menu."
    )


__all__ = [
    "GETTING_STARTED_CUSTOM",
    "GETTING_STARTED_MENU",
    "GETTING_STARTED_OPTIONS",
    "getting_started_skills",
    "load_getting_started_block",
]
