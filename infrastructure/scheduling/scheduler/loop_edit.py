"""Validated definition patches for recurring loops, preserving execution identity."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from config.constants.ci_repair import CI_REPAIR_REPORT_BUILDER
from infrastructure.scheduling.scheduler.cron_expression import (
    build_cron_trigger,
    cap_cron_at_most_hourly,
)
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_DESCRIPTION_PARAM,
    LOOP_EMAIL_ACCOUNT_PARAM,
    LOOP_EMAIL_TO_PARAM,
    LOOP_GROUP_ID_PARAM,
    LOOP_MODE_PARAM,
    LOOP_MODES,
    LOOP_PROMPT_PARAM,
    LOOP_REPORT_PARAM,
    LOOP_SKILL_PARAM,
    LOOP_SLACK_CHAT_ID_PARAM,
    LOOP_TELEGRAM_CHAT_ID_PARAM,
    LOOP_TEMPLATE_PARAM,
    LOOP_TIME_PARAM,
)
from infrastructure.scheduling.scheduler.loops import (
    LoopMutation,
    cron_for_time,
    email_recipients,
    loop_time_label,
    normalize_loop_channels,
    summarize_loops,
)
from infrastructure.scheduling.scheduler.operation_log import record_scheduler_loop_operation
from infrastructure.scheduling.scheduler.runner import compute_next_run
from infrastructure.scheduling.scheduler.storage import patch_task_definitions
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind

#: Where a loop delivers: the channel list and each channel's destination.
#: A destination left out of an edit keeps its current value.
DELIVERY_FIELDS = ("channels", "slack_chat_id", "telegram_chat_id", "email_to", "email_account")
_FIELDS = frozenset(
    {"name", "prompt", "cron", "timezone", "window_hours", "mode", *DELIVERY_FIELDS}
)
_DESTINATION_PARAMS = {
    "slack_chat_id": (Provider.SLACK, LOOP_SLACK_CHAT_ID_PARAM),
    "telegram_chat_id": (Provider.TELEGRAM, LOOP_TELEGRAM_CHAT_ID_PARAM),
}


def loop_prompt_editable(task: ScheduledTask) -> bool:
    """Allow prompt/mode edits only for ordinary manual loops with stored instructions."""
    return task.kind is TaskKind.MANUAL_LOOP and not any(
        task.params.get(key, "").strip()
        for key in (LOOP_TEMPLATE_PARAM, LOOP_REPORT_PARAM, LOOP_SKILL_PARAM)
    )


def validate_loop_changes(changes: Mapping[str, str]) -> dict[str, str]:
    """Normalize a field patch without reading credentials or writing task state."""
    unknown = changes.keys() - _FIELDS
    if unknown:
        raise ValueError(f"unsupported loop edit fields: {', '.join(sorted(unknown))}")
    result = {key: value.strip() for key, value in changes.items()}
    for key in ("name", "prompt", "cron", "timezone", "channels"):
        if key in result and not result[key]:
            raise ValueError(f"{key.replace('_', ' ')} is required")
    if "mode" in result and result["mode"] not in LOOP_MODES:
        raise ValueError(f"mode must be one of {', '.join(LOOP_MODES)}")
    if "window_hours" in result:
        try:
            window = int(result["window_hours"])
        except ValueError as exc:
            raise ValueError("lookback must be a whole number of hours") from exc
        if window < 1:
            raise ValueError("lookback must be at least 1 hour")
        result["window_hours"] = str(window)
    return result


def loop_changes(
    *,
    time: str = "",
    weekdays: bool = False,
    cron: str = "",
    timezone: str = "",
    prompt: str = "",
    name: str = "",
    mode: str = "",
) -> dict[str, str]:
    """The edit patch for the fields given; a ``time`` becomes a daily (or weekday) cron.

    Raises ``ValueError`` when nothing would change or ``time`` and ``cron`` are both set.
    """
    if time.strip() and cron.strip():
        raise ValueError("give either a time or a cron, not both")
    changes = {
        key: value.strip()
        for key, value in (
            ("cron", " ".join(cron.split())),
            ("timezone", timezone),
            ("prompt", prompt),
            ("name", name),
            ("mode", mode),
        )
        if value.strip()
    }
    if time.strip():
        changes["cron"] = cron_for_time(time, weekdays=weekdays)
    if not changes:
        raise ValueError("say what to change: time, cron, timezone, prompt, name or mode")
    return validate_loop_changes(changes)


def _definition_patch(
    task: ScheduledTask, changes: Mapping[str, str], *, loop_id: str, now: datetime | None
) -> Mapping[str, object]:
    if (task.params.get(LOOP_GROUP_ID_PARAM, "").strip() or task.id) != loop_id:
        raise ValueError("the loop group changed while editing; open /loops edit again")
    if task.kind is TaskKind.WORK_ITEM_REMINDER:
        raise ValueError("one-shot reminders cannot be edited with /loops edit")
    if {"prompt", "mode"}.intersection(changes) and not loop_prompt_editable(task):
        raise ValueError("this loop uses managed instructions; edit its name or schedule instead")
    patch: dict[str, object] = {}
    for key in ("name", "timezone"):
        if key in changes:
            patch[key] = changes[key]
    if "window_hours" in changes:
        patch["window_hours"] = int(changes["window_hours"])

    params = dict(task.params)
    if "prompt" in changes:
        previous_description = " ".join(params.get(LOOP_PROMPT_PARAM, "").split())[:120]
        if params.get(LOOP_DESCRIPTION_PARAM, "") == previous_description:
            params[LOOP_DESCRIPTION_PARAM] = " ".join(changes["prompt"].split())[:120]
        params[LOOP_PROMPT_PARAM] = changes["prompt"]
    if "mode" in changes:
        params[LOOP_MODE_PARAM] = changes["mode"]
    if "channels" in changes:
        _apply_delivery(task, params, changes)
    if "cron" in changes or "timezone" in changes:
        timezone = changes.get("timezone", task.timezone)
        cron = changes.get("cron", task.cron)
        build_cron_trigger(cron, timezone)
        if "cron" in changes:
            if (
                task.kind is TaskKind.MANUAL_LOOP
                and params.get(LOOP_REPORT_PARAM, "") != CI_REPAIR_REPORT_BUILDER
            ):
                cron = cap_cron_at_most_hourly(cron, timezone)
            patch["cron"] = cron
            time_label = loop_time_label(cron)
            if time_label:
                params[LOOP_TIME_PARAM] = time_label
            else:
                params.pop(LOOP_TIME_PARAM, None)
        prospective = task.model_copy(update=patch)
        patch["next_run"] = compute_next_run(prospective, now) if task.enabled else None
    if params != task.params:
        patch["params"] = params
    return patch


def _apply_delivery(
    task: ScheduledTask, params: dict[str, str], changes: Mapping[str, str]
) -> None:
    """Point the loop at the channels in ``changes``, validated as a new loop's would be."""
    if task.kind is not TaskKind.MANUAL_LOOP:
        raise ValueError("only manual loops can change where they deliver")

    def current(field: str, param: str) -> str:
        return changes[field] if field in changes else params.get(param, "")

    slack = current("slack_chat_id", LOOP_SLACK_CHAT_ID_PARAM)
    telegram = current("telegram_chat_id", LOOP_TELEGRAM_CHAT_ID_PARAM)
    email_to = current("email_to", LOOP_EMAIL_TO_PARAM)
    email_account = current("email_account", LOOP_EMAIL_ACCOUNT_PARAM)
    providers = normalize_loop_channels(
        changes["channels"].split(","),
        telegram_chat_id=telegram,
        slack_chat_id=slack,
        email_to=email_to,
        email_account=email_account,
    )
    params[LOOP_CHANNELS_PARAM] = ",".join(provider.value for provider in providers)
    for field, (provider, param) in _DESTINATION_PARAMS.items():
        value = (slack if field == "slack_chat_id" else telegram).strip()
        if provider in providers and value:
            params[param] = value
        else:
            params.pop(param, None)
    if Provider.EMAIL in providers:
        params[LOOP_EMAIL_TO_PARAM] = ",".join(email_recipients(email_to))
        params[LOOP_EMAIL_ACCOUNT_PARAM] = email_account.strip()
    else:
        params.pop(LOOP_EMAIL_TO_PARAM, None)
        params.pop(LOOP_EMAIL_ACCOUNT_PARAM, None)


def edit_loop(
    task_ids: tuple[str, ...],
    changes: Mapping[str, str],
    *,
    loop_id: str,
    store_path: Path | None = None,
    now: datetime | None = None,
) -> tuple[LoopMutation | None, str]:
    """Apply one atomic group edit to the latest definitions without recreating tasks."""
    try:
        normalized = validate_loop_changes(changes)
        if not normalized:
            return None, "no changes"
        updated = patch_task_definitions(
            task_ids,
            lambda task: _definition_patch(task, normalized, loop_id=loop_id, now=now),
            store_path,
            group_id=loop_id,
        )
    except ValueError as exc:
        return None, str(exc)
    if updated is None:
        return None, "the loop changed or was removed while editing; open /loops edit again"
    summaries = summarize_loops(updated, now=now)
    if len(summaries) != 1:
        return None, "the loop group changed while editing"
    mutation = LoopMutation(summary=summaries[0], task_ids=tuple(task.id for task in updated))
    record_scheduler_loop_operation(
        "scheduled_loop_edited", mutation.summary, task_ids=mutation.task_ids
    )
    return mutation, ""


__all__ = [
    "DELIVERY_FIELDS",
    "edit_loop",
    "loop_changes",
    "loop_prompt_editable",
    "validate_loop_changes",
]
