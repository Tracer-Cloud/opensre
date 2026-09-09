"""Enter the onboarding skill on interactive launch and through ``/demo``.

The host enters the master skill directly — no autosubmitted prompt, no model
step, no tool-event render — so the skill's ``pre_execute`` menu is the first
thing painted. The user's pick is the first message the model sees.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.tools import ActionToolScope
from infrastructure.analytics.capture import capture_onboarding_demo_prompted
from infrastructure.analytics.source import is_test_run
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from tools.interactive_shell.actions.skill_entry import enter_skill, pre_execute_queued_menu

if TYPE_CHECKING:
    from rich.console import Console

    from surfaces.interactive_shell.session import Session

logger = logging.getLogger(__name__)


class _StartupTtyProbe:
    """The one slash-port method a host-side skill entry needs: can the picker render?

    Startup runs before the slash dispatcher is wired, and importing the REPL
    slash adapter here would cycle through ``command_registry`` (``/demo``
    imports this module), so the entry carries its own TTY answer.
    """

    def tty_interactive(self) -> bool:
        return repl_tty_interactive()


def should_offer_demo() -> bool:
    """Offer onboarding on interactive launches outside the test harness."""
    return not is_test_run() and repl_tty_interactive()


def offer_demo(session: Session, console: Console | None = None, *, force: bool = False) -> bool:
    """Enter the master skill so its ``pre_execute`` menu opens; prints nothing itself."""
    if not repl_tty_interactive() or (not force and not should_offer_demo()):
        return False
    if session.pending_user_choice is not None or session.terminal.pending_prompt_default:
        return False
    scope = ActionToolScope(
        session=session,
        console=console,
        is_tty=True,
        slash_ports=_StartupTtyProbe(),
    )
    result = enter_skill(ONBOARDING_SKILL_NAME, scope)
    if not result.get("ok") or not pre_execute_queued_menu(result.get("pre_execute", [])):
        # A master skill without its menu is a skill bug; do not fall back to a model turn.
        logger.warning(
            "Onboarding skill did not queue its menu: %s",
            result.get("error", "pre_execute queued no menu"),
        )
        session.active_skill = None
        session.active_skill_tools = ()
        return False
    try:
        capture_onboarding_demo_prompted()
    except Exception:
        logger.debug("Could not capture onboarding startup.", exc_info=True)
    return True
