"""Action tools: schedule a recurring prompt from a chat, and stop one.

A user who asks in Slack for recurring work ("every weekday at 9, check my repos
for failed branches") gets the loop created in that turn, delivering back to
the conversation it was asked in. No confirmation round-trip: the request is
the confirmation, and a bare "yes" in a Slack DM starts a new session that
would not remember an offer.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import AgentToolContext, SideEffectLevel
from core.tool_framework import tool
from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODE_REPORT, LOOP_MODES
from infrastructure.scheduling.scheduler.loops import (
    SUPPORTED_MANUAL_LOOP_CHANNELS,
    create_manual_loop,
    delete_loop,
    resolve_loop_summary,
    set_loop_enabled,
)
from infrastructure.scheduling.scheduler.reload_signal import request_scheduler_reload
from infrastructure.scheduling.scheduler.storage import get_task
from infrastructure.scheduling.scheduler.task_visibility import visible_to_current_scope
from infrastructure.scheduling.scheduler.types import Provider
from tools.system.scheduled_loops.destination import conversation_destination

SCHEDULE_TOOL_NAME = "schedule_prompt_loop"
STOP_TOOL_NAME = "stop_scheduled_loop"
_SOURCE = "system"
_SUPPORTED_CHANNELS = frozenset(provider.value for provider in SUPPORTED_MANUAL_LOOP_CHANNELS)
_STOP_ACTIONS = ("pause", "resume", "delete")


def _session(context: AgentToolContext | None) -> Any:
    if context is None:
        return None
    try:
        from core.agent_harness.tools import action_context_from_agent_context

        return action_context_from_agent_context(context).session
    except RuntimeError:
        return None


def _destination(context: AgentToolContext | None) -> tuple[str, str] | str:
    """The chat this turn came from, the local inbox off chat, or an error."""
    provider, chat_id = conversation_destination(_session(context))
    if not provider:
        return Provider.INTERACTIVE_SHELL.value, ""
    if provider not in _SUPPORTED_CHANNELS or not chat_id:
        return f"Scheduled prompts cannot deliver to {provider} yet; ask from Slack or Telegram."
    return provider, chat_id


@tool(
    name=SCHEDULE_TOOL_NAME,
    source=_SOURCE,
    display_name="Schedule a recurring prompt",
    description=(
        "Create a recurring scheduled prompt that runs on its own and posts each result "
        "back to the conversation this request came from (the local inbox off chat). "
        "Use it when the user asks outright for recurring work: 'every weekday at 9 "
        "check my repos for failed branches', 'each morning summarize open PRs'. "
        "Write prompt as the complete standalone instruction for one run, naming the "
        "repositories or services involved; it runs without this conversation. "
        "Runs fire at most once an hour. mode 'report' (default) reads and reports; "
        "'agent' may also act with tools (open PRs, comment). Do not use it for a "
        "one-off reminder (work_task_add) or a CI repair loop (schedule_ci_repair_loop)."
    ),
    use_cases=[
        "Every weekday at 9am check Tracer-Cloud/opensre for failed branches and tell me",
        "Each morning summarize open pull requests waiting on review",
        "Every Monday report flaky CI jobs in owner/repo",
    ],
    anti_examples=[
        "Remind me once tomorrow (use work_task_add with remind_at)",
        "Repair a failing pull request until it is green (use schedule_ci_repair_loop)",
        "The user only wondered whether something could be scheduled",
    ],
    outputs={
        "loop_id": "Id to pause, resume or delete the loop with stop_scheduled_loop",
        "cron": "The schedule stored, after the at-most-hourly cap",
        "next_run": "Next run in UTC",
        "delivers_to": "Where each run's result is posted",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "minLength": 1,
                "description": "Standalone instruction executed on every run.",
            },
            "time": {
                "type": "string",
                "description": "Daily run time such as '09:00' or '9am'. Use this or cron.",
            },
            "weekdays": {
                "type": "boolean",
                "default": False,
                "description": "With time: run Monday to Friday only.",
            },
            "cron": {
                "type": "string",
                "description": "Five-field cron, e.g. '0 9 * * mon-fri'. Use this or time.",
            },
            "timezone": {
                "type": "string",
                "default": "UTC",
                "description": "IANA timezone the time or cron is in, e.g. 'Europe/Warsaw'.",
            },
            "name": {"type": "string", "description": "Short name; derived from the prompt."},
            "mode": {"type": "string", "enum": list(LOOP_MODES), "default": LOOP_MODE_REPORT},
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
)
def schedule_prompt_loop(
    prompt: str,
    time: str = "",
    weekdays: bool = False,
    cron: str = "",
    timezone: str = "UTC",
    name: str = "",
    mode: str = LOOP_MODE_REPORT,
    context: AgentToolContext | None = None,
) -> dict[str, Any]:
    destination = _destination(context)
    if isinstance(destination, str):
        return {"ok": False, "error": destination}
    provider, chat_id = destination
    requested_cron = " ".join(cron.split())
    try:
        created = create_manual_loop(
            name=name,
            prompt=prompt,
            time_text=time,
            timezone=timezone,
            weekdays=weekdays,
            cron=requested_cron,
            channels=(provider,),
            slack_chat_id=chat_id if provider == Provider.SLACK.value else "",
            telegram_chat_id=chat_id if provider == Provider.TELEGRAM.value else "",
            mode=mode,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    request_scheduler_reload()
    task = created.task
    delivers_to = f"{provider}:{chat_id}" if chat_id else "the local loop inbox"
    result: dict[str, Any] = {
        "ok": True,
        "loop_id": task.id,
        "name": task.name,
        "cron": task.cron,
        "timezone": task.timezone,
        "next_run": created.next_run,
        "delivers_to": delivers_to,
        "response_text": (
            f"Scheduled “{task.name}” ({task.cron} {task.timezone}); next run {created.next_run}. "
            f"Results post to {'this conversation' if chat_id else delivers_to}. "
            f"Loop id {task.id}."
        ),
    }
    if requested_cron and task.cron != requested_cron:
        result["note"] = f"Capped from {requested_cron}: scheduled prompts run at most hourly."
    return result


@tool(
    name=STOP_TOOL_NAME,
    source=_SOURCE,
    display_name="Pause, resume or delete a scheduled loop",
    description=(
        "Pause, resume or delete one scheduled loop by its id or exact name (from "
        "list_scheduled_loops or schedule_prompt_loop). Pause keeps it for later; delete "
        "removes it."
    ),
    use_cases=[
        "Stop the repo failure check you scheduled",
        "Pause the morning PR summary for now",
        "Turn the weekly CI report back on",
    ],
    anti_examples=["Cancel a turn or background job running now (use task_cancel)"],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    input_schema={
        "type": "object",
        "properties": {
            "loop": {"type": "string", "minLength": 1, "description": "Loop id or exact name."},
            "action": {"type": "string", "enum": list(_STOP_ACTIONS), "default": "pause"},
        },
        "required": ["loop"],
        "additionalProperties": False,
    },
)
def stop_scheduled_loop(loop: str, action: str = "pause") -> dict[str, Any]:
    if action not in _STOP_ACTIONS:
        return {"ok": False, "error": f"action must be one of {', '.join(_STOP_ACTIONS)}"}
    summary, error = resolve_loop_summary(loop)
    tasks = [get_task(task_id) for task_id in summary.task_ids] if summary is not None else []
    # Another organization's loop is reported exactly like a missing one.
    if summary is None or not all(t is not None and visible_to_current_scope(t) for t in tasks):
        return {"ok": False, "error": error or f"loop {loop.strip()!r} not found"}
    if action == "delete":
        mutation, error = delete_loop(summary.id)
    else:
        mutation, error = set_loop_enabled(summary.id, enabled=action == "resume")
    if mutation is None:
        return {"ok": False, "error": error}
    request_scheduler_reload()
    verb = {"pause": "Paused", "resume": "Resumed", "delete": "Deleted"}[action]
    return {
        "ok": True,
        "loop_id": summary.id,
        "action": action,
        "response_text": f"{verb} “{summary.name}”.",
    }


__all__ = [
    "SCHEDULE_TOOL_NAME",
    "STOP_TOOL_NAME",
    "schedule_prompt_loop",
    "stop_scheduled_loop",
]
