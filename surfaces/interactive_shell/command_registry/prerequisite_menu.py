"""Run a host-action row of the setup menu a skill prerequisite opened.

The menu's rows are not answers for the model. "Set up on this machine" maps to
the setup wizard's slash command, which ``/choose`` runs like any mapped row; the
wizard resumes the parked turn when it finishes. The other rows land here: open
the OpenSRE app, re-check after connecting there, or drop the parked turn.
"""

from __future__ import annotations

import logging
import webbrowser
from enum import StrEnum

from rich.console import Console
from rich.markup import escape

from config.constants.skill_prerequisites import (
    PREREQUISITE_CONTINUE_ACTION,
    PREREQUISITE_OPEN_APP_ACTION,
    PREREQUISITE_SKIP_ACTION,
    prerequisite_service_label,
)
from core.agent_harness.spi.session_state import clear_setup_resume
from infrastructure.analytics.capture import capture_browser_open_requested
from infrastructure.terminal import theme as ui_theme
from integrations.account_integrations import account_setup_url, load_account_integrations
from surfaces.interactive_shell.command_registry.setup_resume import (
    ResumeOutcome,
    resume_after_setup,
)
from surfaces.interactive_shell.runtime import Session
from tools.interactive_shell.actions.skill_prerequisite_gate import queue_prerequisite_menu

logger = logging.getLogger(__name__)


class MenuStep(StrEnum):
    """What ``/choose`` does after a host action ran."""

    DONE = "done"
    """The action queued whatever comes next, if anything."""

    ASK_AGAIN = "ask_again"
    """The setup menu is queued again; show it in this ``/choose`` turn."""

    LEAVE = "leave"
    """The user declined setup: close the menu and leave the skill."""


def run_prerequisite_action(
    session: Session, console: Console, action: str, service: str
) -> MenuStep:
    """Run setup-menu host action ``action`` for ``service``."""
    if action == PREREQUISITE_OPEN_APP_ACTION:
        _open_app(console, service)
        queue_prerequisite_menu(session, service)
        return MenuStep.ASK_AGAIN
    if action == PREREQUISITE_CONTINUE_ACTION:
        return _continue(session, console, service)
    if action == PREREQUISITE_SKIP_ACTION:
        clear_setup_resume(session)
        return MenuStep.LEAVE
    logger.debug("Ignoring unknown setup-menu action %r", action)
    return MenuStep.DONE


def _open_app(console: Console, service: str) -> None:
    label = escape(prerequisite_service_label(service))
    url = account_setup_url()
    if not url:
        console.print(
            f"[{ui_theme.WARNING}]Could not build the OpenSRE app link. "
            f"Open the app, connect {label}, then continue.[/]"
        )
        return
    opened = False
    try:
        opened = bool(webbrowser.open(url))
    except (webbrowser.Error, OSError):
        opened = False
    finally:
        capture_browser_open_requested(target="integration_setup", opened=opened)
    lead = f"Opened {escape(url)}." if opened else f"Open {escape(url)}."
    console.print(f"[{ui_theme.DIM}]{lead} Connect {label} there, then continue.[/]")


def _continue(session: Session, console: Console, service: str) -> MenuStep:
    # The app may have just gained the connection: ask it now instead of
    # serving the cached snapshot, then re-resolve this session's integrations.
    load_account_integrations(refresh=True)
    session.refresh_integration_state()
    outcome = resume_after_setup(session, console, service=service)
    if outcome is ResumeOutcome.STILL_MISSING:
        return MenuStep.ASK_AGAIN
    if outcome in (ResumeOutcome.NOTHING_PARKED, ResumeOutcome.DROPPED):
        label = escape(prerequisite_service_label(service))
        console.print(
            f"[{ui_theme.DIM}]Nothing is waiting on {label} setup — type a request to continue.[/]"
        )
    return MenuStep.DONE


__all__ = ["MenuStep", "run_prerequisite_action"]
