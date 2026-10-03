"""Safe-boundary handling for in-flight session-goal controls."""

from __future__ import annotations

from core.agent_harness import SessionCore
from core.agent_harness.spi.cancel import HostCancelReason
from core.agent_harness.spi.session_state import session_terminal


def mark_inflight_goal_control(session: SessionCore, reason: HostCancelReason) -> None:
    """Remember that the queued slash command follows boundary handling."""
    terminal = session_terminal(session)
    if terminal is None:
        return
    key = reason.value
    terminal.pending_inflight_goal_controls[key] = (
        terminal.pending_inflight_goal_controls.get(key, 0) + 1
    )


def consume_inflight_goal_control(session: SessionCore, reason: HostCancelReason) -> bool:
    """Consume one queued slash command already handled at the boundary."""
    terminal = session_terminal(session)
    if terminal is None:
        return False
    key = reason.value
    pending = terminal.pending_inflight_goal_controls.get(key, 0)
    if pending <= 0:
        return False
    if pending == 1:
        terminal.pending_inflight_goal_controls.pop(key)
    else:
        terminal.pending_inflight_goal_controls[key] = pending - 1
    return True


__all__ = [
    "consume_inflight_goal_control",
    "mark_inflight_goal_control",
]
