"""Host-owned prerequisites a skill needs before it starts, and the setup menu shown without them.

The cards stay human-owned. ``SKILL_PREREQUISITES`` names, per skill, the checks
the host runs when the skill is entered; the checks themselves are registered by
id in ``infrastructure.harness_providers`` by the layer that can answer them.
An unmet check opens the setup menu below instead of the workflow.

``prerequisite_section`` is prepended to a gated skill's body, the same way
success criteria are appended, for the case where setup is still needed after
the skill started.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple

from config.constants.github import GITHUB_SETUP_SLASH_INVOKE
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    CONNECTING_SLACK_SKILL_NAME,
    DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME,
    ONBOARDING_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)


class SkillPrerequisite(NamedTuple):
    """One check a skill needs, and the integration whose setup satisfies it."""

    check: str
    service: str


#: A GitHub REST token resolves for the turn's integrations: the OpenSRE app's
#: GitHub connection, the local integration, or ``GITHUB_TOKEN`` / ``GH_TOKEN`` /
#: ``GITHUB_MCP_AUTH_TOKEN``.
GITHUB_REST_TOKEN_CHECK = "github_rest_token"

_GITHUB_REST_TOKEN = SkillPrerequisite(check=GITHUB_REST_TOKEN_CHECK, service="github")

#: Skill name -> the prerequisites checked on entry. Every getting-started skill
#: and the onboarding master are listed, even with no prerequisite, so adding a
#: demo is a decision about its setup. The delegate demo checks the hosted
#: gateway in its own first step.
SKILL_PREREQUISITES: Mapping[str, tuple[SkillPrerequisite, ...]] = MappingProxyType(
    {
        ONBOARDING_SKILL_NAME: (),
        ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME: (_GITHUB_REST_TOKEN,),
        SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME: (_GITHUB_REST_TOKEN,),
        DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME: (),
        CONNECTING_SLACK_SKILL_NAME: (),
    }
)

#: Analytics ``reason_code`` for a prerequisite whose credential is missing.
PREREQUISITE_CREDENTIAL_MISSING = "credential_missing"

#: Display name per service; a service not listed shows its id.
PREREQUISITE_SERVICE_LABELS: Mapping[str, str] = MappingProxyType({"github": "GitHub"})

# Setup menu. ``{service}`` is the display name.
PREREQUISITE_MENU_TITLE = "Connect {service} to continue"
PREREQUISITE_MENU_NOTE = (
    "This needs a {service} token, and none is set up yet. "
    "Once {service} is connected, OpenSRE picks up where it stopped."
)
PREREQUISITE_STILL_MISSING_NOTE = "{service} is still not connected: no usable token was found."
PREREQUISITE_OPEN_APP_OPTION = "Connect {service} in the OpenSRE app (recommended)"
PREREQUISITE_LOCAL_SETUP_OPTION = "Set up {service} on this machine"
PREREQUISITE_CONTINUE_OPTION = "I've connected {service} — continue"
PREREQUISITE_SKIP_OPTION = "Not now"
PREREQUISITE_SKIPPED_NOTE = "Skipped {service} setup — type a request to continue."

#: Prefix of the menu's host actions in ``PendingUserChoice.commands``, shaped
#: ``prerequisite:<action>:<service>``. Not slash commands: ``/choose`` acts on
#: them itself. The local-setup row maps to the real setup slash command.
PREREQUISITE_ACTION_PREFIX = "prerequisite:"
PREREQUISITE_OPEN_APP_ACTION = "open-app"
PREREQUISITE_CONTINUE_ACTION = "continue"
PREREQUISITE_SKIP_ACTION = "skip"

CONNECT_INTEGRATIONS_HEADING = "## Connect integrations first"

_SECTION_BY_CHECK: Mapping[str, str] = MappingProxyType(
    {
        GITHUB_REST_TOKEN_CHECK: (
            "The host checks for a usable GitHub token before this skill starts, so "
            "it normally begins with GitHub connected. CI tools in this skill read "
            "GitHub with that token (the OpenSRE app's GitHub connection, the local "
            "integration, `GITHUB_TOKEN`, or `GH_TOKEN`). `integrations verify github` "
            "checks the GitHub MCP endpoint, which these tools do not use. A verify "
            "result other than `passed` does not block them.\n"
            "\n"
            "If a GitHub tool still reports a missing token, call "
            f"`{GITHUB_SETUP_SLASH_INVOKE}` and end the turn so the shell opens "
            "setup. Once GitHub is connected, the shell resubmits the message you "
            "were answering; continue the same step from it. Do not wait for MCP "
            "verification to pass first.\n"
        ),
    }
)

_OTHER_INTEGRATIONS = (
    "For any other integration, call "
    '`slash_invoke(command="/integrations", args=["setup", "<service>"])`.\n'
)


def prerequisite_service_label(service: str) -> str:
    """The name a user knows ``service`` by."""
    return PREREQUISITE_SERVICE_LABELS.get(service, service)


def prerequisite_section(name: str) -> str:
    """Markdown the host prepends to skill ``name``; empty when it has no prerequisite."""
    sections = dict.fromkeys(
        _SECTION_BY_CHECK[prerequisite.check]
        for prerequisite in SKILL_PREREQUISITES.get(name, ())
        if prerequisite.check in _SECTION_BY_CHECK
    )
    if not sections:
        return ""
    return "\n".join((CONNECT_INTEGRATIONS_HEADING, "", *sections, _OTHER_INTEGRATIONS))


__all__ = [
    "CONNECT_INTEGRATIONS_HEADING",
    "GITHUB_REST_TOKEN_CHECK",
    "PREREQUISITE_ACTION_PREFIX",
    "PREREQUISITE_CONTINUE_ACTION",
    "PREREQUISITE_CONTINUE_OPTION",
    "PREREQUISITE_CREDENTIAL_MISSING",
    "PREREQUISITE_LOCAL_SETUP_OPTION",
    "PREREQUISITE_MENU_NOTE",
    "PREREQUISITE_MENU_TITLE",
    "PREREQUISITE_OPEN_APP_ACTION",
    "PREREQUISITE_OPEN_APP_OPTION",
    "PREREQUISITE_SERVICE_LABELS",
    "PREREQUISITE_SKIPPED_NOTE",
    "PREREQUISITE_SKIP_ACTION",
    "PREREQUISITE_SKIP_OPTION",
    "PREREQUISITE_STILL_MISSING_NOTE",
    "SKILL_PREREQUISITES",
    "SkillPrerequisite",
    "prerequisite_section",
    "prerequisite_service_label",
]
