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
from infrastructure.scheduling.scheduler.types import ScheduledTask, TaskRun

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


def _loop_row(loop: LoopSummary, run: TaskRun | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": loop.id,
        "name": loop.name,
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
    lines = [f"{len(rows)} scheduled loops, {active} active."]
    for row in rows:
        latest = row.get("latest_run")
        outcome = f"; last run {latest['status']}" if latest else ""
        when = f"; next {row['next_run']}" if row["enabled"] and row["next_run"] else ""
        lines.append(
            f"- {row['name']} ({row['status']}, {row['cron']} {row['timezone']}{outcome}{when})"
        )
    return "\n".join(lines)


@tool(
    name=TOOL_NAME,
    source="system",
    display_name="List scheduled loops",
    description=(
        "List every scheduled loop this process's scheduler knows: CI repair loops, "
        "reliability loops, reminders and other recurring tasks, each with its schedule, "
        "whether it is enabled, and its newest run's outcome. Use this to answer which "
        "scheduled tasks exist or run here; it reads the task store directly. Read-only."
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
        "loops": "One row per loop: id, name, kind, cron, enabled, status, next_run, latest_run",
        "count": "How many loops were listed",
        "store_missing": "True when no task store file exists yet (nothing was ever scheduled)",
        "response_text": "One line per loop with its status, schedule and last outcome",
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
