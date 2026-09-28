"""Host-owned setup check shown before local GitHub onboarding skills.

The cards stay human-owned. This block is prepended when a skill is loaded,
the same way success criteria are appended, so a fresh install is told to
connect GitHub before it calls a tool that needs a token.
"""

from __future__ import annotations

from config.constants.github import (
    GITHUB_INTEGRATION_SETUP_CLI,
    GITHUB_INTEGRATION_SETUP_SLASH,
)
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)

CONNECT_INTEGRATIONS_HEADING = "## Connect integrations first"

#: Onboarding skills whose first GitHub call fails closed on a machine with no token.
GITHUB_ONBOARDING_SKILLS: frozenset[str] = frozenset(
    {
        ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
        SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
    }
)

_SECTION = (
    f"{CONNECT_INTEGRATIONS_HEADING}\n"
    "\n"
    "Do this before any other tool in this skill.\n"
    "\n"
    "1. Call `cli_exec` with `integrations verify github`.\n"
    "2. When the status is not `passed`, call `slash_invoke` with command "
    f"`{GITHUB_INTEGRATION_SETUP_SLASH}` and end the turn. The shell opens the wizard. "
    "Wait until the user finishes. Do not ask them to leave the shell and type the "
    "command by hand.\n"
    "3. On the next turn, verify again. Continue this skill only after verify reports "
    "`passed`.\n"
    "\n"
    "Use the same two calls for any other integration this skill needs: "
    "`integrations verify <service>`, then `slash_invoke` with `/integrations setup <service>`.\n"
)

# Step 3 of the analysis card still says to file a missing token as a coverage gap.
# That is what stopped the report after the user connected GitHub. This paragraph
# is prepended only on that skill so the loaded instructions resume the same repo.
_ANALYSIS_RESUME = (
    "\n"
    "Step 3 calls `analyze_github_ci_reliability` for the repository already chosen. "
    f"If it reports a missing token, show `{GITHUB_INTEGRATION_SETUP_CLI}` and open it "
    "with the `slash_invoke` call above. After setup finishes, call "
    "`analyze_github_ci_reliability` again with the same owner, repo, and days. "
    "A missing token is not a coverage gap. Do not ask the user to retry in a new "
    "session or leave the analysis blocked.\n"
)


def prerequisite_section(name: str) -> str:
    """Markdown the host prepends so onboarding connects GitHub before its tools."""
    if name not in GITHUB_ONBOARDING_SKILLS:
        return ""
    if name == ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME:
        return _SECTION + _ANALYSIS_RESUME
    return _SECTION
