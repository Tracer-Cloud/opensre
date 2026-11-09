"""Final interactive-session persistence after runtime shutdown."""

from __future__ import annotations

import logging

from core.agent_harness import SessionManager
from core.agent_harness.spi.cancel import HostCancelReason
from core.agent_harness.spi.session_goal import SessionGoal, apply_session_goal_control
from infrastructure.terminal.prompt_support import ctrl_c_exit_interrupted
from infrastructure.turn_host.session_lock import session_execution_lock
from surfaces.interactive_shell.runtime.core.state import ReplState
from surfaces.interactive_shell.runtime.exit_control import record_inflight_shell_exit
from surfaces.interactive_shell.session import Session

logger = logging.getLogger(__name__)


def close_repl_session(session: Session, state: ReplState) -> None:
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
            # ``close`` flushes too, but a teardown Ctrl+C raises wherever it
            # lands and could unwind before reaching it. Persist here, where a
            # ``finally`` guarantees it: ``flush`` never raises.
            manager.flush(session)
        # Never wait on the closing memory pass: it is an LLM call over the whole
        # transcript (seconds, and worse on a resumed session), and holding the
        # user's terminal for a background nicety is not a trade worth making.
        # It goes to the shared daemon worker, the way gateway rotation does it.
        # An exit the user already interrupted does not even start one.
        manager.close(
            session,
            extract_memory=not ctrl_c_exit_interrupted(),
            wait_for_memory_extraction=False,
        )


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
