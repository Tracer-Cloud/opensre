"""Pause onboarding until the signed-in organization has GitHub in the web app.

The menu is host-owned. A local GitHub token does not satisfy it. After the
user connects GitHub, the same menu's continue action re-enters the skill.
"""

from __future__ import annotations

from typing import Any

from config.constants.skills import (
    GITHUB_ONBOARDING_ACTION_PREFIX,
    GITHUB_ONBOARDING_CONTINUE_OPTION,
    GITHUB_ONBOARDING_MENU_TITLE,
    GITHUB_ONBOARDING_OPEN_OPTION,
    ONBOARDING_SKILL_NAMES,
)
from core.agent_harness.spi.session_state import (
    PendingUserChoice,
    session_terminal,
    set_auto_command,
)
from core.agent_harness.tools import ActionToolScope
from integrations.account_integrations import (
    account_github_connection,
    github_onboarding_setup_url,
)

_CHOOSE_COMMAND = "/choose"
_QUEUED_INSTRUCTION = (
    "GitHub is not connected for this organization. A menu is queued that opens "
    "the OpenSRE app and resumes this skill after GitHub is connected. End the "
    "turn now without narrating and without calling ask_user_choice."
)


def github_onboarding_action(kind: str, skill_name: str) -> str:
    """``commands`` value for one menu row. Not a slash command."""
    return f"{GITHUB_ONBOARDING_ACTION_PREFIX}{kind}:{skill_name}"


def parse_github_onboarding_action(command: str) -> tuple[str, str] | None:
    """Return ``(open|continue, skill)`` when ``command`` is one of this menu's actions."""
    if not command.startswith(GITHUB_ONBOARDING_ACTION_PREFIX):
        return None
    rest = command[len(GITHUB_ONBOARDING_ACTION_PREFIX) :]
    kind, separator, skill_name = rest.partition(":")
    if (
        kind not in {"open", "continue"}
        or not separator
        or skill_name not in ONBOARDING_SKILL_NAMES
    ):
        return None
    return kind, skill_name


def github_onboarding_note(
    url: str,
    *,
    still_missing: bool = False,
    unreachable: bool = False,
) -> str:
    """One-line explainer painted with the setup menu."""
    if unreachable:
        lead = "The OpenSRE app could not be reached to check GitHub."
    elif still_missing:
        lead = "GitHub is still not connected for this organization."
    else:
        lead = "Onboarding needs GitHub connected for this organization."
    return f"{lead} Open {url}, connect GitHub, then continue."


def queue_github_onboarding_menu(session: Any, skill_name: str, *, note: str) -> None:
    """Store the setup menu and queue ``/choose`` so the shell opens it."""
    session.pending_user_choice = PendingUserChoice(
        title=GITHUB_ONBOARDING_MENU_TITLE,
        options=(GITHUB_ONBOARDING_OPEN_OPTION, GITHUB_ONBOARDING_CONTINUE_OPTION),
        note=note,
        commands={
            GITHUB_ONBOARDING_OPEN_OPTION: github_onboarding_action("open", skill_name),
            GITHUB_ONBOARDING_CONTINUE_OPTION: github_onboarding_action("continue", skill_name),
        },
        custom_answer=False,
    )
    set_auto_command(session, _CHOOSE_COMMAND)


def block_onboarding_until_github_connected(name: str, ctx: Any) -> dict[str, Any] | None:
    """Return a skill-entry result when onboarding must wait; None when it may start.

    Only a confirmed missing web-app connection blocks. Signed out, already
    connected, and an unreachable app all let the skill start.
    """
    if name not in ONBOARDING_SKILL_NAMES:
        return None
    if account_github_connection() != "missing":
        return None
    url = github_onboarding_setup_url()
    if not url:
        return None
    session = getattr(ctx, "session", None)
    if session is None or not _can_queue_menu(ctx):
        return _text_block(name, url)
    queue_github_onboarding_menu(
        session,
        name,
        note=github_onboarding_note(url),
    )
    return {
        "ok": True,
        "name": name,
        "summary": "waiting for GitHub to be connected in the OpenSRE app",
        "content": _QUEUED_INSTRUCTION,
        "entry_menu": {
            "ok": True,
            "tool": "ask_user_choice",
            "menu": "queued",
            "instruction": _QUEUED_INSTRUCTION,
        },
    }


def _can_queue_menu(ctx: Any) -> bool:
    if not isinstance(ctx, ActionToolScope):
        return False
    if ctx.is_tty is False or session_terminal(ctx.session) is None:
        return False
    ports = ctx.slash_ports
    return ports is not None and bool(ports.tty_interactive())


def _text_block(name: str, url: str) -> dict[str, Any]:
    instruction = (
        f"Connect GitHub at {url} and then load this skill again. "
        "Do not start the workflow until that connection is active."
    )
    return {
        "ok": True,
        "name": name,
        "summary": "GitHub must be connected in the OpenSRE app before this skill",
        "content": instruction,
        "entry_menu": {
            "ok": False,
            "tool": "ask_user_choice",
            "menu": "unavailable",
            "instruction": instruction,
        },
    }


__all__ = [
    "block_onboarding_until_github_connected",
    "github_onboarding_action",
    "github_onboarding_note",
    "parse_github_onboarding_action",
    "queue_github_onboarding_menu",
]
