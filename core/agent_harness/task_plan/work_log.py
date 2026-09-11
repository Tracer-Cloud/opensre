"""Host-owned per-step work log for post-execution plan breakdowns.

The model-writable ``update_plan`` schema stays status-only. While a step is
``in_progress``, the host records short tool/activity lines under that step.
When the plan completes, the REPL prints a one-shot checklist with those lines
assigned to each step — separate from the live pinned overlay.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan
from core.agent_harness.task_plan.progress import PLAN_STATUS_GLYPH, format_plan_header
from infrastructure.safety.terminal_output import strip_terminal_controls

_MAX_WORK_LINES_PER_STEP = 12
_MAX_WORK_LINE_CHARS = 120
#: A lone work line is trimmed to this so it never wraps under the ``↳`` indent.
_WORK_LINE_DISPLAY_CHARS = 72


def _work_kind(entry: str) -> str:
    """Leading label of a work entry used to group same-kind calls.

    Two capitalized words (``GitHub CLI``) count as the kind; otherwise the
    first word (``Execute``, ``opensre``, ``Python``).
    """
    words = entry.split()
    if not words:
        return entry
    if len(words) >= 2 and words[0][:1].isupper() and words[1][:1].isupper():
        return f"{words[0]} {words[1]}"
    return words[0]


def _grouped_work_lines(entries: list[str]) -> list[str]:
    """Collapse consecutive same-kind entries into ``↳ Kind · N calls``.

    A lone call keeps its (trimmed) text so a single action still reads
    concretely; a run of the same kind reads as one grouped summary.
    """
    lines: list[str] = []
    start = 0
    while start < len(entries):
        kind = _work_kind(entries[start])
        end = start
        while end < len(entries) and _work_kind(entries[end]) == kind:
            end += 1
        count = end - start
        if count >= 2:
            lines.append(f"      ↳ {kind} · {count} calls")
        else:
            entry = entries[start]
            if len(entry) > _WORK_LINE_DISPLAY_CHARS:
                entry = f"{entry[: _WORK_LINE_DISPLAY_CHARS - 1].rstrip()}…"
            lines.append(f"      ↳ {entry}")
        start = end
    return lines


def _step_texts(plan: TaskPlan) -> tuple[str, ...]:
    return tuple(item.step for item in plan.steps)


def sync_task_plan_work_for_plan(session: Any, plan: TaskPlan) -> None:
    """Resize or reset the work log when the checklist identity changes."""
    texts = _step_texts(plan)
    prior_texts = getattr(session, "task_plan_work_step_texts", None)
    if prior_texts != texts:
        session.task_plan_work = [[] for _ in plan.steps]
        session.task_plan_work_step_texts = texts
        session.task_plan_breakdown_emitted = False
        return
    work = getattr(session, "task_plan_work", None)
    if not isinstance(work, list) or len(work) != len(plan.steps):
        resized: list[list[str]] = [[] for _ in plan.steps]
        if isinstance(work, list):
            for index, lines in enumerate(work):
                if index >= len(resized):
                    break
                if isinstance(lines, list):
                    resized[index] = [str(line) for line in lines]
        session.task_plan_work = resized
    if not plan.all_completed:
        session.task_plan_breakdown_emitted = False


def in_progress_step_index(plan: TaskPlan) -> int | None:
    """0-based index of the sole ``in_progress`` step, if any."""
    for index, item in enumerate(plan.steps):
        if item.status is PlanStepStatus.IN_PROGRESS:
            return index
    return None


def record_task_plan_work(session: Any, line: str, *, step_index: int | None = None) -> None:
    """Append a work line under a plan step.

    Defaults to the current ``in_progress`` step. Pass ``step_index`` to attribute
    work to a specific checklist row.
    No-op when there is no plan, no target step, or the line is empty.
    Caps lines per step so a chatty turn stays readable.
    """
    plan = getattr(session, "task_plan", None)
    if plan is None or not plan.steps:
        return
    sync_task_plan_work_for_plan(session, plan)
    index = step_index if step_index is not None else in_progress_step_index(plan)
    if index is None:
        return
    index = max(0, min(int(index), len(plan.steps) - 1))
    cleaned = strip_terminal_controls(line).strip()
    if not cleaned:
        return
    if len(cleaned) > _MAX_WORK_LINE_CHARS:
        cleaned = f"{cleaned[: _MAX_WORK_LINE_CHARS - 1].rstrip()}…"
    work: list[list[str]] = session.task_plan_work
    bucket = work[index]
    if cleaned in bucket:
        return
    if len(bucket) >= _MAX_WORK_LINES_PER_STEP:
        return
    bucket.append(cleaned)


def format_task_plan_breakdown(
    plan: TaskPlan,
    work_by_step: list[list[str]] | None = None,
) -> str:
    """Plain-text post-execution checklist with work lines under each step.

    Empty steps still appear (so the user sees the full plan). Steps that
    gathered work list each line under a ``↳`` marker.
    """
    work = work_by_step or []
    header = format_plan_header(plan)
    if plan.all_completed:
        header = f"Plan complete · {plan.total}/{plan.total}"
    lines = [header]
    for index, item in enumerate(plan.steps):
        mark = PLAN_STATUS_GLYPH[item.status]
        lines.append(f"  {mark} {item.step}")
        step_work = work[index] if index < len(work) else []
        lines.extend(_grouped_work_lines(step_work))
    return "\n".join(lines)


def take_completed_plan_breakdown(session: Any) -> str:
    """Return the one-shot breakdown when the plan is complete; else ``\"\"``.

    Marks the breakdown as emitted so a later caller in the same workload does
    not reprint it. A new checklist identity resets that latch via
    :func:`sync_task_plan_work_for_plan`.
    """
    plan = getattr(session, "task_plan", None)
    if plan is None or not plan.steps or not plan.all_completed:
        return ""
    if getattr(session, "task_plan_breakdown_emitted", False):
        return ""
    sync_task_plan_work_for_plan(session, plan)
    work = getattr(session, "task_plan_work", None)
    text = format_task_plan_breakdown(plan, work if isinstance(work, list) else None)
    session.task_plan_breakdown_emitted = True
    return text


__all__ = [
    "format_task_plan_breakdown",
    "in_progress_step_index",
    "record_task_plan_work",
    "sync_task_plan_work_for_plan",
    "take_completed_plan_breakdown",
]
