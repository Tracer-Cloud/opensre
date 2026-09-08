"""Agent-callable durable work-item tools."""

from __future__ import annotations

import json
from typing import Any

from core.domain.types.tools import ToolSurface
from core.domain.work_items import (
    WORK_ITEM_PRIORITIES,
    WORK_ITEM_STATUSES,
    WorkItemChannelTarget,
    WorkItemPriority,
    WorkItemUpdates,
    add_work_item,
    complete_work_items,
    list_work_items,
    make_work_item,
    prioritize_work_items,
    resolve_work_item_selector,
    update_work_item,
    work_items_path,
)
from core.tool import AgentToolContext, SideEffectLevel
from core.tool_framework import tool
from infrastructure.scheduling.scheduler.storage import add_task as add_scheduled_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tools.system.work_items._evidence import map_work_task_list, map_work_task_prioritize
from tools.system.work_items.delivery import delivery_targets, invalid_delivery_targets
from tools.system.work_items.reminders import schedule_item_reminder
from tools.system.work_items.results import (
    added_result,
    complete_result,
    item_summary,
    list_result,
    priority_result,
    scheduled_result,
    update_result,
)
from tools.system.work_items.validation import (
    DEFAULT_PRIORITY_LIMIT,
    DEFAULT_WORK_ITEM_LIMIT,
    normalize_limit,
    normalize_priority,
    normalize_selectors,
    normalize_status_filter,
    valid_priority_detail,
    valid_status_detail,
    validate_datetime_arg,
)


def _work_items_available(_sources: dict[str, dict[str, Any]]) -> bool:
    return True


@tool(
    name="work_task_add",
    display_name="Add task",
    source="knowledge",
    description=(
        "Create a durable human work item, todo, reminder, or hackathon task. Use this for "
        "'add task ...', 'todo ...', 'remind me ...', and follow-ups the user wants tracked. "
        "If remind_at is provided, also schedule a one-shot reminder to the selected channel."
    ),
    use_cases=[
        "User asks to add a task or todo",
        "User asks OpenSRE to remind a channel about a follow-up",
        "Conversation surfaces a clear action item that should be tracked",
    ],
    anti_examples=[
        "User asks about runtime background jobs or synthetic task ids (use /tasks or task_cancel)",
        "User asks to create a GitHub issue rather than a local work item",
    ],
    tags=("safe", "fast", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    accepts_runtime_context=True,
    is_available=_work_items_available,
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short task title.", "minLength": 1},
            "project": {"type": "string", "description": "Optional project/category."},
            "priority": {
                "type": "string",
                "enum": list(WORK_ITEM_PRIORITIES),
                "default": WorkItemPriority.NORMAL.value,
            },
            "owner": {"type": "string", "description": "Optional person/team responsible."},
            "notes": {"type": "string", "description": "Optional supporting detail."},
            "due_at": {"type": "string", "description": "Optional ISO-like due datetime."},
            "remind_at": {"type": "string", "description": "Optional ISO-like reminder datetime."},
            "channel_provider": {
                "type": "string",
                "description": "Optional reminder provider: telegram, slack, discord, rocketchat.",
            },
            "channel_id": {"type": "string", "description": "Optional reminder chat/channel id."},
            "channel_targets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "telegram, slack, discord, or rocketchat.",
                        },
                        "chat_id": {
                            "type": "string",
                            "description": "Target chat/channel id; optional for Slack webhook delivery.",
                        },
                    },
                    "required": ["provider"],
                    "additionalProperties": False,
                },
                "description": "Optional list of reminder targets for fan-out delivery.",
            },
            "timezone": {
                "type": "string",
                "default": "UTC",
                "description": "IANA timezone for naive remind_at values.",
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    },
)
def work_task_add(
    title: str,
    project: str = "",
    priority: str = WorkItemPriority.NORMAL.value,
    owner: str = "",
    notes: str = "",
    due_at: str = "",
    remind_at: str = "",
    channel_provider: str = "",
    channel_id: str = "",
    channel_targets: list[dict[str, str]] | None = None,
    timezone: str = "UTC",
    context: AgentToolContext | None = None,
) -> dict[str, Any]:
    if not str(title or "").strip():
        return {"error": "empty_title", "detail": "title must be a non-empty string"}
    parsed_priority = normalize_priority(priority)
    if parsed_priority is None:
        return {"error": "invalid_priority", "detail": valid_priority_detail()}
    for field_name, value in (("due_at", due_at), ("remind_at", remind_at)):
        error = validate_datetime_arg(value, field=field_name)
        if error is not None:
            return error
    targets = delivery_targets(
        provider=channel_provider,
        chat_id=channel_id,
        channel_targets=channel_targets,
        context=context,
    )
    invalid_targets = invalid_delivery_targets(targets)
    if invalid_targets:
        return {
            "error": "invalid_delivery_target",
            "detail": "; ".join(invalid_targets),
        }
    if remind_at and not targets:
        return {
            "error": "missing_delivery_target",
            "detail": (
                "remind_at needs channel_targets or channel_provider, unless the turn came from "
                "a gateway chat with an active channel"
            ),
        }
    item = add_work_item(
        title=title,
        priority=parsed_priority,
        project=project,
        owner=owner,
        notes=notes,
        due_at=due_at,
        remind_at=remind_at,
        channel_provider=targets[0].provider if targets else "",
        channel_id=targets[0].chat_id if targets else "",
        channel_targets=targets,
        source="agent",
    )
    scheduled = schedule_item_reminder(
        item,
        targets=targets,
        timezone=timezone or "UTC",
    )
    return added_result(item, scheduled_task_id=scheduled.id if scheduled else "")


@tool(
    name="work_task_list",
    display_name="List tasks",
    source="knowledge",
    description=(
        "List durable human work items. Use when the user asks for all tasks still to do, "
        "hackathon task overview, completed tasks, blocked tasks, or project-specific todos."
    ),
    tags=("safe", "fast", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    is_available=_work_items_available,
    input_schema={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "open, completed, blocked, deferred, active, or all.",
                "default": "open",
            },
            "project": {"type": "string", "description": "Optional exact project filter."},
            "owner": {"type": "string", "description": "Optional exact owner filter."},
            "limit": {"type": "integer", "default": DEFAULT_WORK_ITEM_LIMIT},
        },
        "required": [],
        "additionalProperties": False,
    },
    evidence_mapper=map_work_task_list,
)
def work_task_list(
    status: str = "open",
    project: str = "",
    owner: str = "",
    limit: int = DEFAULT_WORK_ITEM_LIMIT,
) -> dict[str, Any]:
    normalized_status = normalize_status_filter(status)
    if normalized_status == "invalid":
        return {"error": "invalid_status", "detail": valid_status_detail()}
    if normalized_status == "active":
        all_items = list_work_items(status=None, project=project, owner=owner)
        filtered = [item for item in all_items if item.is_active]
    else:
        filtered = list_work_items(status=normalized_status, project=project, owner=owner)
    max_items = normalize_limit(limit)
    return list_result(filtered[:max_items], total=len(filtered))


@tool(
    name="work_task_complete",
    display_name="Complete task",
    source="knowledge",
    description=(
        "Mark one or more durable work items as completed by id, display id, title, or title fragment. "
        "Use when the user says a task is done or asks to mark tasks Y and Z completed."
    ),
    tags=("safe", "fast", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    is_available=_work_items_available,
    input_schema={
        "type": "object",
        "properties": {
            "selectors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Task ids, display ids, titles, or title fragments.",
            }
        },
        "required": ["selectors"],
        "additionalProperties": False,
    },
)
def work_task_complete(selectors: list[str]) -> dict[str, Any]:
    normalized = normalize_selectors(selectors)
    if not normalized:
        return {
            "error": "empty_selectors",
            "detail": "selectors must include at least one task id or title",
        }
    return complete_result(complete_work_items(normalized))


@tool(
    name="work_task_update",
    display_name="Update task",
    source="knowledge",
    description=(
        "Update a durable work item's status, priority, owner, project, due time, reminder time, "
        "notes, or reminder channel."
    ),
    tags=("safe", "fast", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    accepts_runtime_context=True,
    is_available=_work_items_available,
    input_schema={
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "Task id, display id, title, or title fragment.",
            },
            "status": {"type": "string", "enum": list(WORK_ITEM_STATUSES)},
            "priority": {"type": "string", "enum": list(WORK_ITEM_PRIORITIES)},
            "owner": {"type": "string"},
            "project": {"type": "string"},
            "due_at": {"type": "string"},
            "remind_at": {"type": "string"},
            "notes": {"type": "string"},
            "channel_provider": {"type": "string"},
            "channel_id": {"type": "string"},
            "channel_targets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string"},
                        "chat_id": {"type": "string"},
                    },
                    "required": ["provider"],
                    "additionalProperties": False,
                },
            },
            "timezone": {"type": "string", "default": "UTC"},
        },
        "required": ["selector"],
        "additionalProperties": False,
    },
)
def work_task_update(
    selector: str,
    status: str = "",
    priority: str = "",
    owner: str = "",
    project: str = "",
    due_at: str = "",
    remind_at: str = "",
    notes: str = "",
    channel_provider: str = "",
    channel_id: str = "",
    channel_targets: list[dict[str, str]] | None = None,
    timezone: str = "UTC",
    context: AgentToolContext | None = None,
) -> dict[str, Any]:
    changes: WorkItemUpdates = {}
    if status:
        normalized_status = normalize_status_filter(status)
        if normalized_status in {"active", "invalid"} or normalized_status is None:
            return {"error": "invalid_status", "detail": valid_status_detail()}
        changes["status"] = normalized_status
    if priority:
        parsed_priority = normalize_priority(priority)
        if parsed_priority is None:
            return {"error": "invalid_priority", "detail": valid_priority_detail()}
        changes["priority"] = parsed_priority
    if owner:
        changes["owner"] = owner
    if project:
        changes["project"] = project
    if due_at:
        changes["due_at"] = due_at
    if remind_at:
        changes["remind_at"] = remind_at
    if notes:
        changes["notes"] = notes
    for field_name, value in (("due_at", due_at), ("remind_at", remind_at)):
        error = validate_datetime_arg(value, field=field_name)
        if error is not None:
            return error
    explicit_targets = delivery_targets(
        provider=channel_provider,
        chat_id=channel_id,
        channel_targets=channel_targets,
    )
    invalid_targets = invalid_delivery_targets(explicit_targets)
    if invalid_targets:
        return {"error": "invalid_delivery_target", "detail": "; ".join(invalid_targets)}
    if explicit_targets:
        changes["channel_targets"] = explicit_targets
        changes["channel"] = (
            explicit_targets[0].to_dict() if explicit_targets else WorkItemChannelTarget().to_dict()
        )
    reminder_targets: list[WorkItemChannelTarget] = []
    if remind_at:
        existing = resolve_work_item_selector(selector)
        if existing.item is None:
            payload: dict[str, Any] = {"error": existing.error or "not_found"}
            if existing.candidates:
                payload["candidates"] = [item_summary(item) for item in existing.candidates]
            return payload
        reminder_targets = delivery_targets(
            provider=channel_provider,
            chat_id=channel_id,
            item=existing.item,
            channel_targets=channel_targets,
            context=context,
        )
        invalid_reminder_targets = invalid_delivery_targets(reminder_targets)
        if invalid_reminder_targets:
            return {
                "error": "invalid_delivery_target",
                "detail": "; ".join(invalid_reminder_targets),
                "task": item_summary(existing.item),
            }
        if not reminder_targets:
            return {
                "error": "missing_delivery_target",
                "detail": "remind_at needs at least one provider target",
                "task": item_summary(existing.item),
            }
    result = update_work_item(selector, changes=changes)
    if result.item is None:
        update_error_payload: dict[str, Any] = {"error": result.error or "not_found"}
        if result.candidates:
            update_error_payload["candidates"] = [item_summary(item) for item in result.candidates]
        return update_error_payload
    scheduled = None
    if remind_at:
        scheduled = schedule_item_reminder(
            result.item,
            targets=reminder_targets,
            timezone=timezone or "UTC",
        )
    result_payload = update_result(result.item)
    if scheduled is not None:
        result_payload["scheduled_task_id"] = scheduled.id
    return result_payload


@tool(
    name="work_task_prioritize",
    display_name="Prioritize tasks",
    source="knowledge",
    description=(
        "Rank durable work items or a supplied candidate list to answer 'what should I focus on next?' "
        "and 'which of these tasks should I pick up?'. Use the returned reasons in the final answer."
    ),
    tags=("safe", "fast", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    is_available=_work_items_available,
    input_schema={
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Optional exact project filter."},
            "owner": {"type": "string", "description": "Optional exact owner filter."},
            "candidates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional task titles supplied by the user.",
            },
            "limit": {"type": "integer", "default": DEFAULT_PRIORITY_LIMIT},
        },
        "required": [],
        "additionalProperties": False,
    },
    evidence_mapper=map_work_task_prioritize,
)
def work_task_prioritize(
    project: str = "",
    owner: str = "",
    candidates: list[str] | None = None,
    limit: int = DEFAULT_PRIORITY_LIMIT,
) -> dict[str, Any]:
    if candidates:
        items = [
            make_work_item(title=title, priority=WorkItemPriority.NORMAL, source="candidate")
            for title in candidates
            if str(title or "").strip()
        ]
    else:
        items = list_work_items(status=None, project=project, owner=owner)
        items = [item for item in items if item.is_active]
    scores = prioritize_work_items(
        items, limit=normalize_limit(limit, default=DEFAULT_PRIORITY_LIMIT)
    )
    return priority_result(scores)


@tool(
    name="work_task_schedule_checkin",
    display_name="Schedule check-in",
    source="knowledge",
    description=(
        "Create a recurring proactive check-in that posts the highest-priority open work items "
        "to a messaging channel on a cron schedule."
    ),
    use_cases=[
        "User asks OpenSRE to check in every morning about hackathon tasks",
        "User asks for proactive reminders in Slack or Telegram on a recurring cadence",
    ],
    tags=("safe", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    accepts_runtime_context=True,
    is_available=_work_items_available,
    input_schema={
        "type": "object",
        "properties": {
            "cron": {
                "type": "string",
                "description": "Five-field cron expression, e.g. 0 9 * * 1-5.",
            },
            "timezone": {"type": "string", "default": "UTC"},
            "provider": {
                "type": "string",
                "description": "telegram, slack, discord, or rocketchat.",
            },
            "chat_id": {"type": "string", "description": "Target chat/channel id."},
            "channel_targets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string"},
                        "chat_id": {"type": "string"},
                    },
                    "required": ["provider"],
                    "additionalProperties": False,
                },
                "description": "Optional fan-out targets; optional chat_id for Slack webhook delivery.",
            },
            "project": {"type": "string", "description": "Optional exact project filter."},
            "limit": {"type": "integer", "default": DEFAULT_PRIORITY_LIMIT},
        },
        "required": ["cron"],
        "additionalProperties": False,
    },
)
def work_task_schedule_checkin(
    cron: str,
    timezone: str = "UTC",
    provider: str = "",
    chat_id: str = "",
    channel_targets: list[dict[str, str]] | None = None,
    project: str = "",
    limit: int = DEFAULT_PRIORITY_LIMIT,
    context: AgentToolContext | None = None,
) -> dict[str, Any]:
    parts = cron.split()
    if len(parts) != 5:
        return {"error": "invalid_cron", "detail": "cron must have exactly 5 fields"}
    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(cron, timezone=timezone or "UTC")
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": "invalid_cron", "detail": str(exc)}

    targets = delivery_targets(
        provider=provider,
        chat_id=chat_id,
        channel_targets=channel_targets,
        context=context,
    )
    invalid_targets = invalid_delivery_targets(targets)
    if invalid_targets:
        return {"error": "invalid_delivery_target", "detail": "; ".join(invalid_targets)}
    if not targets:
        return {
            "error": "missing_delivery_target",
            "detail": "at least one provider target is required unless this is running inside a gateway chat",
        }
    primary = targets[0]
    task = ScheduledTask(
        kind=TaskKind.WORK_ITEM_CHECKIN,
        cron=cron,
        timezone=timezone or "UTC",
        provider=Provider(primary.provider),
        chat_id=primary.chat_id,
        params={
            "store_path": str(work_items_path()),
            "project": project.strip(),
            "limit": str(normalize_limit(limit, default=DEFAULT_PRIORITY_LIMIT)),
            "delivery_targets": json.dumps(
                [target.to_dict() for target in targets], separators=(",", ":")
            ),
        },
    )
    added = add_scheduled_task(task)
    return scheduled_result(
        added.id,
        cron=added.cron,
        timezone=added.timezone,
        provider=added.provider.value,
        chat_id=added.chat_id,
        delivery_targets=[target.to_dict() for target in targets],
    )


__all__ = [
    "work_task_add",
    "work_task_complete",
    "work_task_list",
    "work_task_prioritize",
    "work_task_schedule_checkin",
    "work_task_update",
]
