"""Plain-text task-plan formatting (prompts, logs, non-TTY).

Rich rendering lives in
``surfaces.interactive_shell.ui.task_plan``.
"""

from __future__ import annotations

from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

VERIFY_LABEL = "(verify)"

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


def step_label(item: PlanStep) -> str:
    """The step text, marked ``(verify)`` only when the step declares it checks the outcome."""
    return f"{item.step} {VERIFY_LABEL}" if item.verifies else item.step


def format_task_plan_plain(plan: TaskPlan) -> str:
    """Checklist with ``Plan · n/m`` header and ✓ / ● / ○ / ⊘ step marks."""
    lines = [format_plan_header(plan)]
    for item in plan.steps:
        mark = PLAN_STATUS_GLYPH[item.status]
        lines.append(f"  {mark} {step_label(item)}")
    return "\n".join(lines)


def task_plan_from_checklist(text: str) -> TaskPlan | None:
    """Rebuild a plan from :func:`format_task_plan_plain` text.

    The header is ignored; step rows are the glyph plus the step text.
    ``(verify)`` is restored onto :attr:`PlanStep.verifies`. Lines that are
    not checklist rows (work notes, a truncated tail) are skipped.
    """
    glyph_status = {glyph: status for status, glyph in PLAN_STATUS_GLYPH.items()}
    verify_suffix = f" {VERIFY_LABEL}"
    steps: list[PlanStep] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("Plan"):
            continue
        glyph, _, rest = stripped.partition(" ")
        status = glyph_status.get(glyph)
        if status is None or not rest:
            continue
        verifies = rest.endswith(verify_suffix)
        if verifies:
            rest = rest[: -len(verify_suffix)].rstrip()
        if not rest:
            continue
        steps.append(PlanStep(step=rest, status=status, verifies=verifies))
    if not steps:
        return None
    return TaskPlan(steps=tuple(steps))


__all__ = [
    "PLAN_STATUS_GLYPH",
    "VERIFY_LABEL",
    "format_plan_header",
    "format_task_plan_plain",
    "step_label",
    "task_plan_from_checklist",
]
