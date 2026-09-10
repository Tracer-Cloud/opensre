"""Action tool that schedules the recurring CI reliability check for one repository."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.github.tools.ci_analytics import loop as ci_loop
from integrations.github.tools.ci_analytics.tool import report_text_from_snapshot

TOOL_NAME = "schedule_ci_reliability_loop"


@tool(
    name="schedule_ci_reliability_loop",
    source="github",
    display_name="Schedule CI reliability check",
    description=(
        "Schedule a recurring CI/CD reliability check for one repository: a local "
        "prompt loop that re-runs the reliability analytics and delivers the report "
        "to this shell's inbox. Weekdays at 08:00 local time unless told otherwise. "
        "Never posts to Slack or any chat channel. Returns the schedule card to "
        "repeat verbatim; with include_report=true, today's saved report (key "
        "results and compare) comes first when a same-day snapshot exists. Never "
        "reads GitHub live."
    ),
    use_cases=[
        "Set up an agent that improves CI/CD reliability over time",
        "Schedule a daily CI reliability report for owner/repo",
        "Watch our CI reliability every weekday morning",
    ],
    anti_examples=[
        "Analyzing CI reliability once, right now (use analyze_github_ci_reliability)",
        "Delivering a report to Slack or Telegram (use propose_scheduled_delivery)",
        "Fixing a failing check (use fix_github_pr_ci)",
    ],
    requires=[],
    outputs={
        "task_id": "Id of the scheduled loop, for /loops commands",
        "next_run": "When the loop fires next",
        "reused": "True when the repository already had this loop",
        "report_as_of": (
            "Snapshot time of the report placed above the card; empty when the card stands alone"
        ),
        "response_text": "The schedule card, preceded by today's report when included",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "Repository owner."},
            "repo": {"type": "string", "description": "Repository name."},
            "time": {
                "type": "string",
                "description": "Local time of day such as 08:00 or 7:30am; default 08:00.",
            },
            "weekdays": {
                "type": "boolean",
                "description": "Run Monday to Friday only (default true); false runs every day.",
            },
            "include_report": {
                "type": "boolean",
                "description": (
                    "Put today's saved reliability report above the schedule card "
                    "(default false). Use when no report was shown this turn."
                ),
            },
        },
        "required": ["owner", "repo"],
        "additionalProperties": False,
    },
    tags=("safe",),
)
def schedule_ci_reliability_loop(
    owner: str,
    repo: str,
    time: str | None = None,
    weekdays: bool | None = None,
    include_report: bool | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        return {"ok": False, "error": "owner and repo are required."}
    try:
        scheduled = ci_loop.schedule_ci_reliability_loop(
            owner,
            repo,
            time_text=(time or ci_loop.DEFAULT_LOOP_TIME).strip() or ci_loop.DEFAULT_LOOP_TIME,
            weekdays=True if weekdays is None else weekdays,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    task = scheduled.loop.task
    card = ci_loop.loop_card(scheduled).markdown()
    report, report_as_of = report_text_from_snapshot(owner, repo) if include_report else ("", "")
    return {
        "ok": True,
        "task_id": task.id,
        "name": task.name,
        "cron": task.cron,
        "timezone": task.timezone,
        "next_run": scheduled.loop.next_run,
        "reused": scheduled.reused,
        "report_as_of": report_as_of if report else "",
        "response_text": f"{report}\n\n{card}" if report else card,
    }


__all__ = ["TOOL_NAME", "schedule_ci_reliability_loop"]
