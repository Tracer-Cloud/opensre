"""Action tool: list the scheduled loops in this process's task store, newest run included."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from infrastructure.scheduling.scheduler.loop_results import latest_loop_runs
from infrastructure.scheduling.scheduler.loops import LoopSummary, list_loop_summaries
from infrastructure.scheduling.scheduler.types import TaskRun

TOOL_NAME = "list_scheduled_loops"

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


def _summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
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
        "response_text": "One line per loop with its status, schedule and last outcome",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    input_schema=_INPUT_SCHEMA,
    tags=("safe",),
)
def list_scheduled_loops(include_disabled: bool = True, **_kwargs: Any) -> dict[str, Any]:
    loops = list_loop_summaries(include_disabled=include_disabled)
    runs = latest_loop_runs(loops)
    rows = [_loop_row(loop, runs.get(loop.id)) for loop in loops]
    return {
        "source": "system",
        "available": True,
        "loops": rows,
        "count": len(rows),
        "response_text": _summary(rows),
    }


__all__ = ["TOOL_NAME", "list_scheduled_loops"]
