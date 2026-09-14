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


def task_plan_awaits_reply(*, task_plan: Any | None) -> bool:
    """True when the plan's current or next step is a ``deliverable`` text reply.

    This is the explicit signal that lets a plan-rejected conclusion reach the
    user; a plan without it keeps every rejected reply off the screen.
    """
    return task_plan is not None and getattr(task_plan, "awaits_reply", False) is True


__all__ = ["task_plan_awaits_reply", "task_plan_blocks_conclusion"]
