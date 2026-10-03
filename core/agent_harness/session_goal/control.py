"""Apply host-requested session-goal controls at an ownership boundary."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.terminal_access import clear_pending_autosubmit
from core.agent_harness.session_goal.goal import clear_session_goal
from core.agent_harness.session_goal.run_until import pause_active_session_goal
from core.agent_harness.turns.host_cancel import HostCancelReason


def apply_session_goal_control(session: Any, reason: HostCancelReason) -> bool:
    """Apply ``reason`` to resumable goal state after its current owner stops."""
    if reason is HostCancelReason.GOAL_PAUSE:
        return pause_active_session_goal(session) is not None
    if reason is HostCancelReason.GOAL_CLEAR:
        clear_pending_autosubmit(session)
        # A prior attempt may have already persisted the goal tombstone before
        # failing to write the paired task-plan tombstone.  Clearing again must
        # still discard that restored plan so the durable control can recover
        # atomically on its next replay.
        clear_session_goal(session)
        return True
    raise ValueError(f"Not a goal control reason: {reason}")


__all__ = ["apply_session_goal_control"]
