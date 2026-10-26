"""Action tools: schedule a recurring prompt from a chat, run one now, change or stop one.

A user who asks in Slack for recurring work ("every weekday at 9, check my repos
for failed branches") gets the loop created in that turn, delivering back to
the conversation it was asked in. No confirmation round-trip: the request is
the confirmation, and a bare "yes" in a Slack DM starts a new session that
would not remember an offer.

A loop asked for in a direct message (or off chat, e.g. a web app session) is
private: it runs as the member who asked, so it can read their own accounts
(their Gmail, Drive, calendar), and only they see, change or run it. One asked
for in a channel is shared, and runs with the workspace's connections only.
"""

from __future__ import annotations

from typing import Any

from config.principal import PrincipalKind
from config.scope_context import current_scope
from core.domain.types.tools import ToolSurface
from core.tool import AgentToolContext, SideEffectLevel
from core.tool_framework import tool
from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODE_REPORT, LOOP_MODES
from infrastructure.scheduling.scheduler.loop_edit import edit_loop, loop_changes
from infrastructure.scheduling.scheduler.loop_owner import loop_is_private, same_member
from infrastructure.scheduling.scheduler.loops import (
    SUPPORTED_MANUAL_LOOP_CHANNELS,
    LoopSummary,
    create_manual_loop,
    delete_loop,
    resolve_loop_summary,
    set_loop_enabled,
)
from infrastructure.scheduling.scheduler.reload_signal import request_scheduler_reload
from infrastructure.scheduling.scheduler.run_now import start_loop_run
from infrastructure.scheduling.scheduler.types import Provider
from tools.system.scheduled_loops.destination import conversation_destination

SCHEDULE_TOOL_NAME = "schedule_prompt_loop"
STOP_TOOL_NAME = "stop_scheduled_loop"
EDIT_TOOL_NAME = "edit_scheduled_loop"
RUN_TOOL_NAME = "run_scheduled_loop"
_SOURCE = "system"
_SUPPORTED_CHANNELS = frozenset(provider.value for provider in SUPPORTED_MANUAL_LOOP_CHANNELS)
_STOP_ACTIONS = ("pause", "resume", "delete")
_PRIVATE_DESCRIPTION = (
    "true: the loop is the requester's own. Each run acts as them, so it can read their "
    "own connected accounts (their Gmail, Drive, calendar, Outlook), and only they can "
    "see, change or run it. false: shared with the workspace, running with the "
    "workspace's connections only."
)
_NOT_OWNER = "Only the member who created this loop can change whether it is private."


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


def _private_conversation(provider: str, chat_id: str) -> bool:
    """Whether a loop asked for here is the asker's own by default.

    A Slack DM (``D…``), a Telegram private chat (a user's positive id) and
    anything off chat are one person's; a channel or group is shared.
    """
    if provider == Provider.SLACK.value:
        return chat_id.startswith("D")
    if provider == Provider.TELEGRAM.value:
        return chat_id.isdigit()
    return provider == Provider.INTERACTIVE_SHELL.value


def _organization_turn() -> bool:
    scope = current_scope()
    return scope is not None and scope.principal.kind == PrincipalKind.ORG


def _acting_member_owns(loop: LoopSummary) -> bool:
    """Whether the member taking this turn created ``loop``; any caller off an organization turn."""
    scope = current_scope()
    if scope is None or scope.principal.kind != PrincipalKind.ORG:
        return True
    return bool(loop.owner) and same_member(scope.actor.id, loop.owner)


def _privacy_note(private: bool) -> str:
    """Who the loop belongs to, for a member of an organization; "" off one."""
    if not _organization_turn():
        return ""
    if private:
        return "It is private: each run uses your own accounts, and only you can see or change it."
    return "It is shared with the workspace and runs with the workspace's connections."


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
        "'agent' may also act with tools (open PRs, comment). A loop asked for in a "
        "direct message is private by default (it runs as the requester, with their own "
        "accounts); in a channel it is shared. Set private true when each run must read "
        "the requester's own accounts (their inbox, calendar, files). Do not use it for a "
        "one-off reminder (work_task_add) or a CI repair loop (schedule_ci_repair_loop)."
    ),
    use_cases=[
        "Every weekday at 9am check Tracer-Cloud/opensre for failed branches and tell me",
        "Each morning summarize open pull requests waiting on review",
        "Every weekday at 8 summarize what came into my inboxes overnight",
    ],
    anti_examples=[
        "Remind me once tomorrow (use work_task_add with remind_at)",
        "Repair a failing pull request until it is green (use schedule_ci_repair_loop)",
        "The user only wondered whether something could be scheduled",
    ],
    outputs={
        "loop_id": "Id to run, change, pause, resume or delete the loop with",
        "cron": "The schedule stored, after the at-most-hourly cap",
        "next_run": "Next run in UTC",
        "delivers_to": "Where each run's result is posted",
        "private": "Whether the loop is the requester's own",
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
            "private": {
                "type": "boolean",
                "description": (
                    f"{_PRIVATE_DESCRIPTION} Defaults to true in a direct message, false "
                    "in a channel."
                ),
            },
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
    private: bool | None = None,
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
            private=_private_conversation(provider, chat_id) if private is None else private,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    request_scheduler_reload()
    task = created.task
    is_private = loop_is_private(task)
    delivers_to = f"{provider}:{chat_id}" if chat_id else "the local loop inbox"
    result: dict[str, Any] = {
        "ok": True,
        "loop_id": task.id,
        "name": task.name,
        "cron": task.cron,
        "timezone": task.timezone,
        "next_run": created.next_run,
        "delivers_to": delivers_to,
        "private": is_private,
        "response_text": " ".join(
            part
            for part in (
                f"Scheduled “{task.name}” ({task.cron} {task.timezone}); "
                f"next run {created.next_run}.",
                f"Results post to {'this conversation' if chat_id else delivers_to}.",
                _privacy_note(is_private),
                f"Loop id {task.id}.",
            )
            if part
        ),
    }
    if requested_cron and task.cron != requested_cron:
        result["note"] = f"Capped from {requested_cron}: scheduled prompts run at most hourly."
    return result


@tool(
    name=RUN_TOOL_NAME,
    source=_SOURCE,
    display_name="Run a scheduled loop now",
    description=(
        "Run one scheduled loop once, now, by its id or exact name (from "
        "list_scheduled_loops), exactly as its next scheduled run would: same "
        "instructions, same destinations, and a private loop runs as its owner with their "
        "own accounts. Its schedule and status do not change, and a paused loop stays "
        "paused. The run starts in the background; its result posts to the loop's "
        "destinations when it finishes, usually within a few minutes."
    ),
    use_cases=[
        "Run my email digest now",
        "Trigger the morning PR summary once so I can see it",
        "Fire the weekly CI report early",
    ],
    anti_examples=[
        "Do the loop's work inside this conversation instead (just do the task)",
        "Schedule a new recurring prompt (use schedule_prompt_loop)",
    ],
    outputs={
        "loop_id": "The loop that started a run",
        "delivers_to": "The channels the result will post to",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    input_schema={
        "type": "object",
        "properties": {
            "loop": {"type": "string", "minLength": 1, "description": "Loop id or exact name."},
        },
        "required": ["loop"],
        "additionalProperties": False,
    },
)
def run_scheduled_loop(loop: str) -> dict[str, Any]:
    # Resolution is scoped: another organization's loop, and another member's
    # private loop, read as not found.
    summary, error = resolve_loop_summary(loop)
    if summary is None:
        return {"ok": False, "error": error}
    error = start_loop_run(summary.id)
    if error:
        return {"ok": False, "error": error}
    channels = ", ".join(summary.channels)
    return {
        "ok": True,
        "loop_id": summary.id,
        "delivers_to": list(summary.channels),
        "response_text": (
            f"Started a run of “{summary.name}”. Its result posts to {channels} when it finishes."
        ),
    }


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
    # Resolution is scoped: another organization's loop reads as not found.
    summary, error = resolve_loop_summary(loop)
    if summary is None:
        return {"ok": False, "error": error}
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


@tool(
    name=EDIT_TOOL_NAME,
    source=_SOURCE,
    display_name="Change a scheduled loop",
    description=(
        "Change one scheduled loop by its id or exact name: its time or cron, timezone, "
        "the instruction each run follows, its name, its mode, or whether it is private. "
        "Only the fields given change; the loop keeps its id, history and destination. "
        "Use it for 'move my morning report to 8:30', 'run it on weekdays only', 'also "
        "include open incidents in the report', 'use Europe/Warsaw time', 'make my email "
        "digest private'. Only the member who created a loop can make it private or "
        "shared; making it private adopts its current instructions and destination, so "
        "tell them what those are."
    ),
    use_cases=[
        "Move the morning report to 8:30 my time",
        "Make the repo check run on weekdays only",
        "Make my inbox digest private so it can read my Gmail",
    ],
    anti_examples=[
        "Pause or delete a loop (use stop_scheduled_loop)",
        "Schedule a new recurring prompt (use schedule_prompt_loop)",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    input_schema={
        "type": "object",
        "properties": {
            "loop": {"type": "string", "minLength": 1, "description": "Loop id or exact name."},
            "time": {
                "type": "string",
                "description": "New daily run time such as '08:30' or '8am'. Use this or cron.",
            },
            "weekdays": {
                "type": "boolean",
                "default": False,
                "description": "With time: run Monday to Friday only.",
            },
            "cron": {"type": "string", "description": "New five-field cron. Use this or time."},
            "timezone": {"type": "string", "description": "New IANA timezone."},
            "prompt": {"type": "string", "description": "New standalone instruction per run."},
            "name": {"type": "string", "description": "New short name."},
            "mode": {"type": "string", "enum": list(LOOP_MODES)},
            "private": {"type": "boolean", "description": _PRIVATE_DESCRIPTION},
        },
        "required": ["loop"],
        "additionalProperties": False,
    },
)
def edit_scheduled_loop(
    loop: str,
    time: str = "",
    weekdays: bool = False,
    cron: str = "",
    timezone: str = "",
    prompt: str = "",
    name: str = "",
    mode: str = "",
    private: bool | None = None,
) -> dict[str, Any]:
    summary, error = resolve_loop_summary(loop)
    if summary is None:
        return {"ok": False, "error": error}
    # A shared loop's instructions and destination may have been set by anyone;
    # only its owner may let it run as them.
    if private is not None and not _acting_member_owns(summary):
        return {"ok": False, "error": _NOT_OWNER}
    try:
        changes = loop_changes(
            time=time,
            weekdays=weekdays,
            cron=cron,
            timezone=timezone,
            prompt=prompt,
            name=name,
            mode=mode,
            private=private,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    mutation, error = edit_loop(summary.task_ids, changes, loop_id=summary.id)
    if mutation is None:
        return {"ok": False, "error": error}
    request_scheduler_reload()
    updated = mutation.summary
    response = (
        f"Updated “{updated.name}” ({updated.cron} {updated.timezone}); "
        f"next run {updated.next_run}."
    )
    if private is not None and _organization_turn():
        response = f"{response} {_privacy_note(updated.private)}"
    return {
        "ok": True,
        "loop_id": updated.id,
        "cron": updated.cron,
        "timezone": updated.timezone,
        "next_run": updated.next_run,
        "private": updated.private,
        "prompt": updated.prompt,
        "delivers_to": list(updated.channels),
        "response_text": response,
    }


__all__ = [
    "EDIT_TOOL_NAME",
    "RUN_TOOL_NAME",
    "SCHEDULE_TOOL_NAME",
    "STOP_TOOL_NAME",
    "edit_scheduled_loop",
    "run_scheduled_loop",
    "schedule_prompt_loop",
    "stop_scheduled_loop",
]
