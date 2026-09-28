"""Host-owned setup check shown before local GitHub onboarding skills.

The cards stay human-owned. This block is prepended when a skill is loaded,
the same way success criteria are appended. It tells the agent how to open
GitHub setup without blocking CI tools that already have a REST token.
"""

from __future__ import annotations

from config.constants.github import GITHUB_SETUP_SLASH_INVOKE
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)

CONNECT_INTEGRATIONS_HEADING = "## Connect integrations first"

#: Onboarding skills that call GitHub before a token may exist.
GITHUB_ONBOARDING_SKILLS: frozenset[str] = frozenset(
    {
        ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
        SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
    }
)

_SECTION = (
    f"{CONNECT_INTEGRATIONS_HEADING}\n"
    "\n"
    "CI tools in this skill read GitHub with a token (the integration token, "
    "`GITHUB_TOKEN`, or `GH_TOKEN`). `integrations verify github` checks the "
    "GitHub MCP endpoint, which these tools do not use. A verify result other "
    "than `passed` does not block them.\n"
    "\n"
    "Call the skill's GitHub tool. If it reports a missing token, call "
    f"`{GITHUB_SETUP_SLASH_INVOKE}` and end the turn so the shell opens setup. "
    "After setup finishes, call that tool again. Do not wait for MCP "
    "verification to pass first.\n"
    "\n"
    "For any other integration, call "
    '`slash_invoke(command="/integrations", args=["setup", "<service>"])`.\n'
)


def prerequisite_section(name: str) -> str:
    """Markdown the host prepends so onboarding can open GitHub setup."""
    if name not in GITHUB_ONBOARDING_SKILLS:
        return ""
    return _SECTION


__all__ = [
    "CONNECT_INTEGRATIONS_HEADING",
    "GITHUB_ONBOARDING_SKILLS",
    "prerequisite_section",
]
