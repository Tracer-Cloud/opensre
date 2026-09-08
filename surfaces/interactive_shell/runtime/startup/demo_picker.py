"""Start the onboarding skill on interactive launch and through ``/demo``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.constants.skills import ONBOARDING_SKILL_NAME
from infrastructure.analytics.capture import capture_onboarding_demo_prompted
from infrastructure.analytics.source import is_test_run
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive

if TYPE_CHECKING:
    from rich.console import Console

    from surfaces.interactive_shell.session import Session

logger = logging.getLogger(__name__)


def should_offer_demo() -> bool:
    """Offer onboarding on interactive launches outside the test harness."""
    return not is_test_run() and repl_tty_interactive()


def offer_demo(session: Session, console: Console | None = None, *, force: bool = False) -> bool:
    """Queue the master skill; its agent turn owns Ask User and child selection."""
    del console
    if not repl_tty_interactive() or (not force and not should_offer_demo()):
        return False
    if session.pending_user_choice is not None or session.terminal.pending_prompt_default:
        return False
    session.terminal.set_auto_command(
        f"Load the {ONBOARDING_SKILL_NAME} skill with skill_view and follow it."
    )
    try:
        capture_onboarding_demo_prompted()
    except Exception:
        logger.debug("Could not capture onboarding startup.", exc_info=True)
    return True
