"""Schedule the daily growth report from a chat: every morning, to a channel or this chat."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from infrastructure.scheduling.scheduler.reload_signal import request_scheduler_reload
from infrastructure.scheduling.scheduler.types import Provider
from integrations.opensre_analytics.audience import audience_refusal
from integrations.opensre_analytics.config import SERVICE
from integrations.opensre_analytics.report import (
    DEFAULT_REPORT_TIME,
    GrowthReportSchedule,
    schedule_growth_report,
)
from integrations.opensre_analytics.tools._sources import analytics_available, origin_params
from integrations.slack.web_client import (
    join_channel,
    normalize_channel_ref,
    resolve_bot_token,
    resolve_channel_id,
)

TOOL_NAME = "schedule_analytics_growth_report"
_OTHER_RECURRING_REPORT = (
    "A recurring report about something other than product growth (use schedule_prompt_loop)"
)


def _slack_channel(channel: str) -> tuple[str, str, str]:
    """``(channel_id, note, error)`` for an internal ``#name`` or ``C…`` id the bot can post to.

    A channel shared with another organization is refused before anything else.
    Joins a public channel so delivery does not fail with ``not_in_channel``.
    A failed join does not block scheduling: the bot may already be a member,
    or someone can invite it, so it becomes a note instead.
    """
    target, token_error = resolve_bot_token()
    if target is None:
        return "", "", token_error or "Slack is not connected, so I cannot post to a channel."
    channel_id, error = resolve_channel_id(target, channel)
    if not channel_id:
        return "", "", error
    refusal = audience_refusal(Provider.SLACK.value, channel_id)
    if refusal is not None:
        return "", "", refusal
    joined, join_error = join_channel(target, channel_id=channel_id)
    note = "" if joined else f"If the bot is not in {channel} yet, /invite it there. ({join_error})"
    return channel_id, note, ""


def _destination(
    channel: str, origin_platform: str, origin_chat_id: str
) -> tuple[str, str, str, str] | str:
    """``(provider, chat_id, label, note)`` the report posts to, or an error."""
    requested = channel.strip()
    if requested:
        channel_id, note, error = _slack_channel(requested)
        if error:
            return error
        _ok, label, _error = normalize_channel_ref(requested)
        return Provider.SLACK.value, channel_id, label or requested, note
    if not origin_platform:
        return Provider.INTERACTIVE_SHELL.value, "", "the local loop inbox", ""
    refusal = audience_refusal(origin_platform, origin_chat_id)
    if refusal is not None:
        return refusal
    return origin_platform, origin_chat_id, "this conversation", ""


@tool(
    name=TOOL_NAME,
    source=SERVICE,
    display_name="Schedule the daily growth report",
    description=(
        "Schedule the OpenSRE daily growth report (new accounts, prompts, active accounts, "
        "installs and activation from OpenSRE Analytics) to post every morning. Posts to "
        "the Slack channel named in channel (e.g. '#product-chat'), else back to this "
        "conversation. Only internal Slack conversations: a channel shared with another "
        "organization, Telegram and other chats are refused. Each run reports the previous UTC day with a fixed format and no "
        "model call. Asking again for the same destination moves the existing schedule "
        "instead of adding a second one. Stop it with stop_scheduled_loop."
    ),
    use_cases=[
        "Post the growth report in #product-chat every morning at 9",
        "Send me daily signups and prompts every weekday at 8:30 Europe/London",
        "Move the morning growth report to 8am",
    ],
    anti_examples=[
        "Show the growth report once, now (use opensre_analytics_daily_growth)",
        _OTHER_RECURRING_REPORT,
    ],
    outputs={
        "loop_id": "Id to pause, resume or delete the loop with stop_scheduled_loop",
        "next_run": "Next run in UTC",
        "delivers_to": "Where each report is posted",
        "rescheduled": "True when an existing schedule for that destination was moved",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    input_schema={
        "type": "object",
        "properties": {
            "time": {
                "type": "string",
                "description": f"Daily run time such as '09:00' or '9am'; default {DEFAULT_REPORT_TIME}.",
            },
            "timezone": {
                "type": "string",
                "description": "IANA timezone for time, e.g. 'Asia/Kolkata'; default UTC.",
            },
            "weekdays": {
                "type": "boolean",
                "description": "Run Monday to Friday only; default every day.",
            },
            "channel": {
                "type": "string",
                "description": (
                    "Slack channel to post to, e.g. '#product-chat'. Omit to post to "
                    "this conversation."
                ),
            },
            "origin_platform": {"type": "string"},
            "origin_chat_id": {"type": "string"},
        },
        "required": [],
        "additionalProperties": False,
    },
    injected_params=("origin_platform", "origin_chat_id"),
    is_available=analytics_available,
    extract_params=origin_params,
)
def schedule_analytics_growth_report(
    time: str | None = None,
    timezone: str | None = None,
    weekdays: bool | None = None,
    channel: str | None = None,
    origin_platform: str | None = None,
    origin_chat_id: str | None = None,
    **_kwargs: object,
) -> dict[str, Any]:
    zone = (timezone or "").strip() or "UTC"
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return {
            "ok": False,
            "error": f"Unknown timezone {zone!r}; use an IANA name such as Asia/Kolkata.",
        }
    destination = _destination(channel or "", origin_platform or "", origin_chat_id or "")
    if isinstance(destination, str):
        return {"ok": False, "error": destination}
    provider, chat_id, label, note = destination
    run_time = (time or "").strip() or DEFAULT_REPORT_TIME
    try:
        scheduled = schedule_growth_report(
            GrowthReportSchedule(
                time_text=run_time,
                timezone=zone,
                weekdays=bool(weekdays),
                provider=provider,
                chat_id=chat_id,
            )
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    request_scheduler_reload()
    task = scheduled.task
    cadence = "every weekday" if weekdays else "every day"
    verb = "Moved" if scheduled.rescheduled else "Scheduled"
    response = (
        f"{verb} the OpenSRE daily growth report: {cadence} at {run_time} {zone}, posting to "
        f"{label}. Next run {task.next_run}. Loop id {task.id}."
    )
    return {
        "ok": True,
        "loop_id": task.id,
        "name": task.name,
        "cron": task.cron,
        "timezone": task.timezone,
        "next_run": task.next_run,
        "delivers_to": label,
        "rescheduled": scheduled.rescheduled,
        "response_text": f"{response} {note}".strip(),
    }
