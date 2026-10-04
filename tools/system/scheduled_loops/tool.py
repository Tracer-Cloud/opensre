"""Action tool: list the scheduled loops in this process's task store, newest run included."""

from __future__ import annotations

from typing import Any

from config.constants.organization import organization_id
from config.principal import PrincipalKind
from config.scope_context import current_scope
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.scheduling.scheduler.loop_results import latest_loop_runs
from infrastructure.scheduling.scheduler.loops import LoopSummary, summarize_loops
from infrastructure.scheduling.scheduler.storage import get_task_store_snapshot
from infrastructure.scheduling.scheduler.types import ScheduledTask, TaskRun, TaskStatus

TOOL_NAME = "list_scheduled_loops"
_SOURCE = "system"
_STORE_UNREADABLE = (
    "The scheduler task store could not be read completely, so the loops are unknown; "
    "this is not an empty schedule. Check the store file under the OpenSRE home."
)

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "include_disabled": {
            "type": "boolean",
            "default": True,
            "description": "Also list loops that are configured but switched off.",
        },
    },
    "additionalProperties": False,
}


def _visible_to_this_turn(task: ScheduledTask) -> bool:
    """A turn bound to an organization sees that organization's tasks and no others.

    Outside any organization scope (the operator's own shell) every task is
    visible. A task without an owner belongs to the deployment's declared
    organization; on a deployment that declares none it is shown to no organization.
    """
    scope = current_scope()
    if scope is None or scope.principal.kind != PrincipalKind.ORG:
        return True
    owner = task.organization or organization_id()
    return owner == scope.principal.id


_PURPOSE_CHARS = 160
_REASON_CHARS = 140
_WEEKDAY_NAMES = {
    "0": "Sundays",
    "1": "Mondays",
    "2": "Tuesdays",
    "3": "Wednesdays",
    "4": "Thursdays",
    "5": "Fridays",
    "6": "Saturdays",
    "7": "Sundays",
    "sun": "Sundays",
    "mon": "Mondays",
    "tue": "Tuesdays",
    "wed": "Wednesdays",
    "thu": "Thursdays",
    "fri": "Fridays",
    "sat": "Saturdays",
}
_WEEKDAYS = {"1-5", "mon-fri"}


def _clip(text: str, limit: int) -> str:
    """One line of ``text``, cut at a word boundary so it reads as a sentence."""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def _purpose(loop: LoopSummary) -> str:
    """What the loop does for the reader: its description, else its prompt's opening sentence."""
    if loop.description:
        return _clip(loop.description, _PURPOSE_CHARS)
    first_sentence = " ".join(loop.prompt.split()).split(". ", 1)[0]
    return _clip(first_sentence, _PURPOSE_CHARS)


def _cadence(cron: str, timezone: str) -> str:
    """How often the loop runs, in words; the exact minute is noise for the reader."""
    parts = cron.split()
    if len(parts) != 5:
        return "on a custom schedule"
    minute, hour, day_of_month, month, day_of_week = parts
    if (day_of_month, month) != ("*", "*"):
        return "on a custom schedule"
    every_day = day_of_week == "*"
    if minute.startswith("*/") and hour == "*" and every_day:
        return f"every {minute[2:]} minutes"
    if minute == "*" and hour == "*" and every_day:
        return "every minute"
    if not minute.isdigit():
        return "on a custom schedule"
    if hour == "*" and every_day:
        return "every hour"
    if hour.startswith("*/") and every_day:
        return f"every {hour[2:]} hours"
    hours = hour.split(",")
    if not all(part.isdigit() for part in hours):
        return "on a custom schedule"
    times = " and ".join(f"{int(part):02d}:{int(minute):02d}" for part in hours)
    zone = f" {timezone}" if timezone else ""
    if every_day:
        return f"daily at {times}{zone}"
    if day_of_week.lower() in _WEEKDAYS:
        return f"weekdays at {times}{zone}"
    days = _WEEKDAY_NAMES.get(day_of_week.lower())
    if days:
        return f"{days} at {times}{zone}"
    return "on a custom schedule"


def _health(loop: LoopSummary, run: TaskRun | None) -> str:
    """Whether the loop is doing its job, with the reason when it is not."""
    if not loop.enabled:
        return "paused" if loop.last_run else "not switched on yet"
    if loop.schedule_error:
        return f"not running: {_clip(loop.schedule_error, _REASON_CHARS)}"
    if run is None:
        return "has not run yet"
    if run.status == TaskStatus.SUCCESS:
        return "last run went fine"
    if run.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        return "running now"
    if run.status == TaskStatus.SKIPPED:
        return "last run was skipped"
    reason = (
        run.error.strip().splitlines()[0].rstrip(" .") if run.error and run.error.strip() else ""
    )
    verb = "stopped early" if run.status == TaskStatus.ABANDONED else "failed"
    return f"last run {verb}: {_clip(reason, _REASON_CHARS)}" if reason else f"last run {verb}"


def _loop_row(loop: LoopSummary, run: TaskRun | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": loop.id,
        "name": loop.name,
        "purpose": _purpose(loop),
        "cadence": _cadence(loop.cron, loop.timezone),
        "health": _health(loop, run),
        "kind": str(loop.kind),
        "prompt": loop.prompt,
        "cron": loop.cron,
        "timezone": loop.timezone,
        "enabled": loop.enabled,
        "status": loop.status,
        "last_run": loop.last_run,
        "next_run": loop.next_run,
        "schedule_error": loop.schedule_error,
    }
    if run is not None:
        row["latest_run"] = {
            "status": str(run.status),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": run.error,
            "report_summary": run.report_summary,
        }
    return row


def _summary(rows: list[dict[str, Any]], *, store_missing: bool) -> str:
    if not rows:
        if store_missing:
            return "No scheduler task store exists here yet, so no loops are configured."
        return "No scheduled loops are configured."
    active = sum(1 for row in rows if row["enabled"])
    needs_attention = sum(1 for row in rows if row["enabled"] and "fail" in row["health"])
    noun = "loop" if len(rows) == 1 else "loops"
    headline = f"{len(rows)} scheduled {noun}, {active} active"
    if needs_attention:
        headline += f", {needs_attention} need attention"
    lines = [headline + "."]
    for row in rows:
        lines.append(f"- {row['name']}: {row['purpose']}")
        lines.append(f"  Runs {row['cadence']}; {row['health']}.")
    return "\n".join(lines)


@tool(
    name=TOOL_NAME,
    source="system",
    display_name="List scheduled loops",
    description=(
        "List every scheduled loop this process's scheduler knows: CI repair loops, "
        "reliability loops, reminders and other recurring tasks, each with its schedule, "
        "whether it is enabled, and its newest run's outcome. Use this to answer which "
        "scheduled tasks exist or run here; it reads the task store directly. Read-only. "
        "When answering, lead with what each loop does for the user: turn its purpose "
        "into one plain sentence about the outcome, then say how often it runs and "
        "whether it is healthy (with the reason when it is not). Write ordinary prose "
        "or bullets, never a code block, and leave out cron expressions, timezones and "
        "timestamps unless the user asks when a loop runs."
    ),
    use_cases=[
        "Which scheduled tasks does this gateway run?",
        "Is the CI repair loop for owner/repo still active, and how did its last run end?",
        "List the recurring reminders and reports configured here",
    ],
    anti_examples=[
        "Scheduling a new loop (use schedule_ci_repair_loop or schedule_ci_reliability_loop)",
        "Reading one repair run's full report (open its result file)",
    ],
    outputs={
        "loops": (
            "One row per loop: id, name, purpose, cadence, health, kind, cron, enabled, "
            "status, next_run, latest_run"
        ),
        "count": "How many loops were listed",
        "store_missing": "True when no task store file exists yet (nothing was ever scheduled)",
        "response_text": "Per loop: what it does, how often it runs, and whether it is healthy",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    input_schema=_INPUT_SCHEMA,
    tags=("safe",),
)
def list_scheduled_loops(include_disabled: bool = True, **_kwargs: Any) -> dict[str, Any]:
    snapshot = get_task_store_snapshot()
    if not snapshot.complete:
        # An unreadable store is not an empty schedule; say so instead of listing nothing.
        return tool_unavailable(_SOURCE, _STORE_UNREADABLE)
    # One read: the rows come from the same validated snapshot the check looked at,
    # narrowed to the organization this turn belongs to.
    own_tasks = [task for task in snapshot.tasks if _visible_to_this_turn(task)]
    loops = summarize_loops(own_tasks, include_disabled=include_disabled)
    runs = latest_loop_runs(loops)
    rows = [_loop_row(loop, runs.get(loop.id)) for loop in loops]
    return {
        "source": _SOURCE,
        "available": True,
        "store_missing": snapshot.missing,
        "loops": rows,
        "count": len(rows),
        "response_text": _summary(rows, store_missing=snapshot.missing),
    }


__all__ = ["TOOL_NAME", "list_scheduled_loops"]
