"""Model-facing ``update_plan`` instruction after a host-normalized write."""

from __future__ import annotations

from core.agent_harness.task_plan.plan import TaskPlan

_STORED = (
    "Plan stored. Keep it current with update_plan; the CURRENT PLAN "
    "block is the durable record when older messages drop."
)
_PLAN_ONLY = " Plan-only: leave every step pending until the user says go."
_AUTHORIZED = " Execution is authorized: the first step is in_progress — run it now."
_CONTINUE = " Continue the in_progress step now — do not end the turn while pending steps remain."
_BLOCKED = (
    " Blocked steps stay blocked — their work did not happen. Do not run tools"
    " to earn a completed mark for them. A blocked step is resolved with the"
    " user, not skipped: before the turn ends, call ask_user_choice naming the"
    " step and its blocker, with options for what would unblock it and one to"
    " leave it. A step the user unblocks goes back to in_progress — that same"
    " step, not a renamed or duplicated copy — and is worked."
)
_DEMOTED = (
    " A step is completed only after its work returned while it was in_progress:"
    " set it in_progress, run the work, then mark it completed. When that work"
    " must not happen (the user said not to run it, or nothing here can), mark"
    " the step blocked and name the reason in explanation instead of running"
    " something else to earn the tick."
    " update_plan, session_goal_*, and loading a skill body do not count as work;"
    " reading a skill reference does."
)
_CLOSED_UNVERIFIED = (
    " The last step closed without a tool and no step marked verifies has run,"
    " so the plan cannot be complete. Either add a step with verifies: true that"
    " checks the result with a tool, run it, then close — or mark the last step"
    " blocked with the reason, and tell the user the result is unverified."
)


def format_update_plan_instruction(
    plan: TaskPlan,
    *,
    plan_only: bool,
    ask_user_turn: bool,
    demoted: tuple[str, ...],
    closed_unverified: bool,
) -> str:
    """Instruction returned on a successful ``update_plan`` write."""
    parts = [_STORED]
    if plan_only:
        parts.append(_PLAN_ONLY)
    elif ask_user_turn and not plan.all_pending:
        parts.append(_AUTHORIZED)
    elif not plan.is_settled:
        parts.append(_CONTINUE)
    if plan.blocked_count:
        parts.append(_BLOCKED)
    if demoted:
        names = "; ".join(demoted)
        parts.append(f" Reset to pending — marked completed before any tool ran for them: {names}.")
        parts.append(_DEMOTED)
    if closed_unverified:
        parts.append(_CLOSED_UNVERIFIED)
    return "".join(parts)


__all__ = ["format_update_plan_instruction"]
