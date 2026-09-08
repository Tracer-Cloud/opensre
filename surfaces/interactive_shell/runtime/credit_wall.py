"""Recovery after a hosted credit wall: the ways-out menu the turn host queues.

Presentation prints the error; this module owns what happens next in the
session (a queued ``/choose``), the way Claude Code offers its billing options
inline after a spend or usage limit.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from core.agent_harness.spi.session_state import (
    PendingUserChoice,
    session_terminal,
    set_auto_command,
)

CREDITS_MENU_TITLE = "Hosted credits are exhausted. What next?"
CREDITS_OPTION_TOP_UP = "Open the usage and top-up page in the browser"
CREDITS_OPTION_SWITCH = "Switch to another LLM provider"
# Every option runs a slash command, never a model turn that would hit the wall again.
CREDITS_MENU_COMMANDS: Mapping[str, str] = MappingProxyType(
    {
        CREDITS_OPTION_TOP_UP: "/account usage",
        CREDITS_OPTION_SWITCH: "/model",
    }
)


def queue_credits_exhausted_menu(session: Any) -> None:
    """Open the ways-out menu once the turn ends; a headless session gets none."""
    if session_terminal(session) is None:
        return
    session.pending_user_choice = PendingUserChoice(
        title=CREDITS_MENU_TITLE,
        options=(CREDITS_OPTION_TOP_UP, CREDITS_OPTION_SWITCH),
        commands=dict(CREDITS_MENU_COMMANDS),
    )
    set_auto_command(session, "/choose")


__all__ = [
    "CREDITS_MENU_COMMANDS",
    "CREDITS_MENU_TITLE",
    "CREDITS_OPTION_SWITCH",
    "CREDITS_OPTION_TOP_UP",
    "queue_credits_exhausted_menu",
]
