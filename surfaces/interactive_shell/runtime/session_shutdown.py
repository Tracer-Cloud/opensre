"""Final interactive-session persistence after runtime shutdown."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console

from core.agent_harness import SessionManager
from core.agent_harness.spi.cancel import HostCancelReason
from core.agent_harness.spi.session_goal import SessionGoal, apply_session_goal_control
from infrastructure.terminal.prompt_support import ctrl_c_exit_interrupted
from infrastructure.turn_host.session_lock import session_execution_lock
from surfaces.interactive_shell.runtime.core.state import ReplState
from surfaces.interactive_shell.runtime.exit_control import record_inflight_shell_exit
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import DIM

logger = logging.getLogger(__name__)

# The closing memory pass is an LLM call, so this runs for seconds with the
# prompt already off screen. Say so, or the shell reads as hung and the user
# starts pressing Ctrl+C at a terminal that looks dead.
_CLOSING_TEXT = "finishing up… (Ctrl+C to skip)"


@contextmanager
def _closing_status(console: Console | None) -> Iterator[None]:
    """Spin while final persistence blocks; silent off a TTY or with no console."""
    if console is None or not console.is_terminal:
        yield
        return
    # Interpolated per call, not at import: the shell picks the user's theme
    # while booting, long after this module loads.
    with console.status(f"[{DIM}]{_CLOSING_TEXT}[/]", spinner="dots", spinner_style=DIM):
        yield


def close_repl_session(
    session: Session,
    state: ReplState,
    *,
    console: Console | None = None,
) -> None:
    """Persist final state unless forced exit left a worker owning the session."""
    if state.has_detached_turn_worker():
        logger.warning(
            "Skipping final session close because detached turn work still owns session state"
        )
        return
    goal_control = state.requested_goal_control()
    manager = SessionManager.for_session(session)
    with session_execution_lock(session.session_id):
        try:
            manager.refresh_from_storage(session)
            if goal_control is not None:
                apply_session_goal_control(session, goal_control)
        finally:
            # ``close`` flushes and only then waits on the closing memory pass,
            # so a teardown Ctrl+C could unwind with the transcript still
            # unwritten. Persist here instead, before anything can block:
            # ``flush`` waits on nothing and never raises.
            manager.flush(session)
        with _closing_status(console):
            # A Ctrl+C here means "stop waiting": the session is already
            # persisted, so only the closing memory pass (an LLM call) is lost.
            manager.close(session, extract_memory=not ctrl_c_exit_interrupted())


def close_repl_session_after_detached_worker(
    session: Session,
    fallback_goal_control: HostCancelReason | None,
    exit_command: str | None,
) -> None:
    """Finalize a session after its detached turn worker releases ownership."""
    manager = SessionManager.for_session(session)
    try:
        with session_execution_lock(session.session_id):
            worker_goal = session.session_goal
            manager.refresh_from_storage(session)
            current_goal = session.session_goal
            if (
                fallback_goal_control is not None
                and isinstance(current_goal, SessionGoal)
                and isinstance(worker_goal, SessionGoal)
                and current_goal.condition == worker_goal.condition
                and current_goal.started_at == worker_goal.started_at
            ):
                apply_session_goal_control(session, fallback_goal_control)
            if exit_command is not None:
                record_inflight_shell_exit(session, exit_command)
            manager.close(session)
    except Exception:
        logger.warning("Deferred session close failed", exc_info=True)


__all__ = ["close_repl_session", "close_repl_session_after_detached_worker"]
