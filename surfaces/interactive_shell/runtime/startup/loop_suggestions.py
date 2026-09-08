"""Suggested-loops startup picker.

When the interactive shell starts and no scheduled tasks ("loops") are
configured on this machine, offer three suggested recurring loops in an inline
dropdown. Picking one auto-submits a canned prompt as the first turn; the
action agent then drives the matching bundled skill or tool guidance
(``github-ci-fix``, GitHub tools, ``morning-report``) through a first demo pass
and the existing ``propose_scheduled_delivery`` → ``/cron add`` confirmation flow. Escape skips
into the normal prompt for this launch — nothing is persisted, so the picker
returns on the next launch while zero loops exist.

This is deterministic startup UI plus a canned prompt: the routing of that
prompt stays with the action agent (no intent heuristics — see
``surfaces/interactive_shell/AGENTS.md``, "Action Selection And Execution").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from infrastructure.analytics.capture import (
    capture_loop_suggestion_prompted,
    capture_loop_suggestion_selected,
    capture_loop_suggestion_skipped,
)
from infrastructure.analytics.source import is_test_run
from infrastructure.terminal.theme import DIM
from surfaces.shared.terminal.components.choice_menu import (
    repl_choose_one,
    repl_tty_interactive,
)

if TYPE_CHECKING:
    from rich.console import Console

    from surfaces.interactive_shell.session import Session

logger = logging.getLogger(__name__)

_MENU_TITLE = "No loops scheduled yet — pick one to set up (Esc to skip)"
_MENU_EXPLAINER = (
    "Loops are scheduled check-ins that post a summary here each weekday. "
    "Picking one runs a first pass now, then offers the schedule — nothing is "
    "saved until you confirm. Manage them anytime with /loops."
)

OPTION_CI_CD = "ci_cd"
OPTION_TASK_MANAGEMENT = "task_management"
OPTION_DAILY_BRIEF = "daily_brief"


@dataclass(frozen=True, slots=True)
class LoopSuggestion:
    """One suggested recurring loop shown in the startup picker."""

    option: str
    """Stable analytics identifier for this suggestion."""

    label: str
    """Menu row shown to the user (name + cadence + one-line pitch)."""

    prompt: str
    """Canned prompt auto-submitted as the first turn when selected."""


LOOP_SUGGESTIONS: tuple[LoopSuggestion, ...] = (
    LoopSuggestion(
        option=OPTION_CI_CD,
        label="CI/CD loop — weekdays 8:00 AM · watch pipelines, surface failing checks",
        prompt=(
            "Report failing CI checks on the current repository's pull requests, "
            "with links and an interactive repair handoff, then set this up as a "
            "read-only recurring weekday check at 8:00 AM."
        ),
    ),
    LoopSuggestion(
        option=OPTION_TASK_MANAGEMENT,
        label="Task management — weekdays 9:00 AM · track issues, PRs, assigned work",
        prompt=(
            "Track my open issues, pull requests, and assigned work in this "
            "repository and flag anything that needs a decision or action, "
            "then set this up as a recurring weekday sweep at 9:00 AM."
        ),
    ),
    LoopSuggestion(
        option=OPTION_DAILY_BRIEF,
        label="Daily brief — weekdays 9:00 AM · start each weekday with a summary",
        prompt="Give me my morning report and schedule it for weekdays at 9:00 AM.",
    ),
)

_SUGGESTIONS_BY_OPTION = {suggestion.option: suggestion for suggestion in LOOP_SUGGESTIONS}


def _no_loops_configured() -> bool:
    from infrastructure.scheduling.scheduler.storage import list_tasks

    return not list_tasks()


def should_offer_loop_suggestions() -> bool:
    """Return True when the suggested-loops picker must run now."""
    if is_test_run():
        return False
    if not repl_tty_interactive():
        return False
    return _no_loops_configured()


def offer_loop_suggestions(session: Session, console: Console | None = None) -> None:
    """Show the picker and queue the chosen suggestion as the first turn.

    Never blocks startup: any unexpected failure is logged and the REPL
    proceeds into the normal prompt.
    """
    try:
        if not should_offer_loop_suggestions():
            return
        capture_loop_suggestion_prompted()
        if console is not None:
            console.print(f"[{DIM}]{_MENU_EXPLAINER}[/]")
        selected = repl_choose_one(
            title=_MENU_TITLE,
            choices=[(suggestion.option, suggestion.label) for suggestion in LOOP_SUGGESTIONS],
        )
        if selected is None:
            capture_loop_suggestion_skipped()
            return
        suggestion = _SUGGESTIONS_BY_OPTION[selected]
        capture_loop_suggestion_selected(option=suggestion.option)
        # Prefill + auto-submit: the prompt echoes the command itself, so no
        # extra announcement print here (it would show the text twice).
        session.terminal.set_auto_command(suggestion.prompt)
    except Exception:
        logger.warning("Suggested-loops startup picker failed.", exc_info=True)


__all__ = [
    "LOOP_SUGGESTIONS",
    "OPTION_CI_CD",
    "OPTION_DAILY_BRIEF",
    "OPTION_TASK_MANAGEMENT",
    "LoopSuggestion",
    "offer_loop_suggestions",
    "should_offer_loop_suggestions",
]
