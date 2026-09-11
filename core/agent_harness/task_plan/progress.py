"""Plain-text task-plan formatting (prompts, logs, non-TTY).

Rich rendering lives in
``surfaces.interactive_shell.ui.task_plan``.
"""

from __future__ import annotations

from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan

PLAN_STATUS_GLYPH: dict[PlanStepStatus, str] = {
    PlanStepStatus.COMPLETED: "✓",
    PlanStepStatus.IN_PROGRESS: "●",
    PlanStepStatus.PENDING: "○",
    PlanStepStatus.BLOCKED: "⊘",
}


def format_plan_header(plan: TaskPlan) -> str:
    """Counter line shared by plain text and the live overlay.

    Once any step is blocked the counter switches from the focused step to the
    completed count and names the blocked count, live and settled alike, so
    ``9/9`` never appears over work that was not done (seven blocked steps with
    the last one active used to read as ``Plan · 9/9``).
    """
    if plan.all_pending:
        return f"Plan ready · 0/{plan.total} executed"
    if plan.blocked_count:
        return f"Plan · {plan.completed_count}/{plan.total} · {plan.blocked_count} blocked"
    return f"Plan · {plan.current_index}/{plan.total}"


def format_task_plan_plain(plan: TaskPlan) -> str:
    """Checklist with ``Plan · n/m`` header and ✓ / ● / ○ / ⊘ step marks."""
    lines = [format_plan_header(plan)]
    for item in plan.steps:
        mark = PLAN_STATUS_GLYPH[item.status]
        lines.append(f"  {mark} {item.step}")
    return "\n".join(lines)


__all__ = ["PLAN_STATUS_GLYPH", "format_plan_header", "format_task_plan_plain"]
