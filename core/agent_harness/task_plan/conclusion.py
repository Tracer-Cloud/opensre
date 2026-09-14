"""Whether a live task plan still blocks ending the action turn."""

from __future__ import annotations

from typing import Any


def task_plan_blocks_conclusion(
    *,
    task_plan: Any | None,
    plan_only: bool,
) -> bool:
    """True when a live execution plan still requires work this turn.

    Plan-only (user asked not to run yet) never blocks. A settled plan — every
    step completed or blocked — never blocks: a blocked step has nothing left
    to run. Otherwise the agent must keep going — stopping with ``●`` on a
    mid-plan step leaves the shell idle while the overlay still shows work.
    """
    if plan_only or task_plan is None:
        return False
    steps = getattr(task_plan, "steps", None)
    if not steps:
        return False
    settled = getattr(task_plan, "is_settled", None)
    if callable(settled):
        return not bool(settled())
    if isinstance(settled, bool):
        return not settled
    return any(getattr(item, "status", None) not in {"completed", "blocked"} for item in steps)


def blocked_steps_await_the_user(session: Any, *, user_answered: bool = False) -> bool:
    """True when a step was newly blocked this turn and the user has not been asked about it.

    A blocked step is resolved with the user, not skipped: the turn ends
    through a question (``ask_user_choice`` queued), never on the block alone.
    On a turn that carries the user's answer they have just been consulted,
    so a step that stays blocked by their choice ends the turn.
    """
    from core.agent_harness.task_plan.evidence import blocked_this_turn

    if user_answered or not blocked_this_turn(session):
        return False
    return getattr(session, "pending_user_choice", None) is None


def task_plan_awaits_reply(*, task_plan: Any | None) -> bool:
    """True when the plan's current or next step is a ``deliverable`` text reply.

    This is the explicit signal that lets a plan-rejected conclusion reach the
    user; a plan without it keeps every rejected reply off the screen.
    """
    return task_plan is not None and getattr(task_plan, "awaits_reply", False) is True


__all__ = ["blocked_steps_await_the_user", "task_plan_awaits_reply", "task_plan_blocks_conclusion"]
