"""The scheduled daily growth report: a deterministic loop tick and its schedule.

No model runs on a tick: the builder reads the analytics app and renders the
same report every morning, so figures are never paraphrased or invented and a
tick costs no LLM credits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

import infrastructure.harness_providers as harness_providers
from config.constants.opensre_analytics import OPENSRE_ANALYTICS_GROWTH_REPORT_BUILDER
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_GROUP_ID_PARAM,
    LOOP_REPORT_PARAM,
    LOOP_SLACK_CHAT_ID_PARAM,
    LOOP_TELEGRAM_CHAT_ID_PARAM,
)
from infrastructure.scheduling.scheduler.loop_edit import edit_loop
from infrastructure.scheduling.scheduler.loops import (
    create_manual_loop,
    cron_for_time,
    set_loop_enabled,
)
from infrastructure.scheduling.scheduler.storage import get_task, list_tasks
from infrastructure.scheduling.scheduler.task_visibility import visible_to_current_scope
from infrastructure.scheduling.scheduler.types import (
    Provider,
    ScheduledTask,
    TaskKind,
    TaskReport,
)
from integrations.opensre_analytics.audience import audience_refusal
from integrations.opensre_analytics.config import (
    SERVICE,
    OpenSREAnalyticsConfig,
    build_opensre_analytics_config,
)
from integrations.opensre_analytics.growth import collect_growth
from integrations.opensre_analytics.render import headline, render_growth_report

DEFAULT_REPORT_TIME = "09:00"
LOOP_NAME = "OpenSRE daily growth report"
LOOP_PROMPT = (
    "Daily OpenSRE growth report for the previous UTC day from OpenSRE Analytics: new "
    "accounts, active accounts, prompts, sessions, tool calls, hosted LLM calls, new "
    "installations and activation. Call opensre_analytics_daily_growth and reply with its "
    "response_text exactly as returned."
)
#: Report argument naming the Slack conversation a tick posts to, re-checked each tick.
_SLACK_CHAT_ARG = "slack_chat_id"
NOT_CONNECTED_TEXT = (
    "Daily growth report skipped: OpenSRE Analytics is not connected. Connect it from "
    "the OpenSRE dashboard (Integrations → OpenSRE Analytics) with the analytics API token."
)


def analytics_config(resolved: Mapping[str, Any]) -> OpenSREAnalyticsConfig | None:
    """The configured analytics connection in resolved integrations, if any."""
    entry = resolved.get(SERVICE)
    if isinstance(entry, OpenSREAnalyticsConfig):
        config = entry
    elif isinstance(entry, BaseModel):
        config = build_opensre_analytics_config(entry.model_dump())
    elif isinstance(entry, Mapping) and entry:
        config = build_opensre_analytics_config(dict(entry))
    else:
        return None
    return config if config.is_configured else None


def build_report(args: Mapping[str, str]) -> TaskReport:
    """Loop tick: read yesterday's figures and render the morning report.

    A Slack destination is checked again on every tick: a channel that has
    since been shared with another organization gets an empty body, which the
    scheduler does not deliver, so it receives nothing. Otherwise returns a
    blocked report naming a missing connection, and a failed one when no
    source answered, so the channel hears either instead of a report of zeros.
    """
    slack_chat_id = args.get(_SLACK_CHAT_ARG, "").strip()
    if slack_chat_id:
        refusal = audience_refusal(Provider.SLACK.value, slack_chat_id)
        if refusal is not None:
            return TaskReport(
                "",
                summary=refusal,
                work_status="blocked",
                error_kind="opensre_analytics_audience_refused",
            )
    config = analytics_config(harness_providers.resolve_integrations())
    if config is None:
        return TaskReport(
            NOT_CONNECTED_TEXT,
            work_status="blocked",
            error_kind="opensre_analytics_not_connected",
        )
    snapshot = collect_growth(config)
    text = render_growth_report(snapshot)
    if not snapshot.available:
        return TaskReport(
            text,
            summary=headline(snapshot),
            work_status="failed",
            error_kind="opensre_analytics_unavailable",
        )
    return TaskReport(text, summary=headline(snapshot))


@dataclass(frozen=True)
class GrowthReportSchedule:
    """Where and when the daily growth report runs."""

    time_text: str = DEFAULT_REPORT_TIME
    timezone: str = "UTC"
    weekdays: bool = False
    provider: str = Provider.INTERACTIVE_SHELL.value
    chat_id: str = ""


@dataclass(frozen=True)
class ScheduledGrowthReport:
    """The stored loop after scheduling, and whether an existing one was moved."""

    task: ScheduledTask
    rescheduled: bool


def _destination(task: ScheduledTask) -> tuple[str, str]:
    provider = task.params.get(LOOP_CHANNELS_PARAM, "").split(",")[0].strip()
    chat_param = {
        Provider.SLACK.value: LOOP_SLACK_CHAT_ID_PARAM,
        Provider.TELEGRAM.value: LOOP_TELEGRAM_CHAT_ID_PARAM,
    }.get(provider)
    return provider, task.params.get(chat_param, "").strip() if chat_param else ""


def _existing_loop(schedule: GrowthReportSchedule, store_path: Path | None) -> ScheduledTask | None:
    """This scope's growth loop that already posts to the requested destination."""
    wanted = (schedule.provider, schedule.chat_id)
    return next(
        (
            task
            for task in list_tasks(store_path)
            if task.kind is TaskKind.MANUAL_LOOP
            and task.params.get(LOOP_REPORT_PARAM) == OPENSRE_ANALYTICS_GROWTH_REPORT_BUILDER
            and _destination(task) == wanted
            and visible_to_current_scope(task)
        ),
        None,
    )


def _reschedule(
    task: ScheduledTask, schedule: GrowthReportSchedule, store_path: Path | None
) -> ScheduledTask:
    group_id = task.params.get(LOOP_GROUP_ID_PARAM, "").strip() or task.id
    cron = cron_for_time(schedule.time_text, weekdays=schedule.weekdays)
    mutation, error = edit_loop(
        (task.id,),
        {"cron": cron, "timezone": schedule.timezone.strip() or "UTC"},
        loop_id=group_id,
        store_path=store_path,
    )
    if mutation is None and error != "no changes":
        raise ValueError(error)
    if not task.enabled:
        resumed, error = set_loop_enabled(group_id, enabled=True, store_path=store_path)
        if resumed is None:
            raise ValueError(error)
    return get_task(task.id, store_path) or task


def schedule_growth_report(
    schedule: GrowthReportSchedule, *, store_path: Path | None = None
) -> ScheduledGrowthReport:
    """Create the daily growth loop, or move the one already posting to that destination.

    One loop per destination: asking again with a new time reschedules it
    instead of posting twice. Raises ``ValueError`` for a time, timezone or
    channel the scheduler rejects.
    """
    existing = _existing_loop(schedule, store_path)
    if existing is not None:
        return ScheduledGrowthReport(
            task=_reschedule(existing, schedule, store_path), rescheduled=True
        )
    created = create_manual_loop(
        name=LOOP_NAME,
        prompt=LOOP_PROMPT,
        time_text=schedule.time_text,
        timezone=schedule.timezone,
        weekdays=schedule.weekdays,
        channels=(schedule.provider,),
        slack_chat_id=schedule.chat_id if schedule.provider == Provider.SLACK.value else "",
        telegram_chat_id=(schedule.chat_id if schedule.provider == Provider.TELEGRAM.value else ""),
        store_path=store_path,
        report=OPENSRE_ANALYTICS_GROWTH_REPORT_BUILDER,
        report_args=(
            {_SLACK_CHAT_ARG: schedule.chat_id}
            if schedule.provider == Provider.SLACK.value
            else None
        ),
    )
    return ScheduledGrowthReport(task=created.task, rescheduled=False)


__all__ = [
    "DEFAULT_REPORT_TIME",
    "LOOP_NAME",
    "LOOP_PROMPT",
    "GrowthReportSchedule",
    "ScheduledGrowthReport",
    "analytics_config",
    "build_report",
    "schedule_growth_report",
]
