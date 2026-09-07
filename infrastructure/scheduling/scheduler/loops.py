"""Loop-friendly view, creation, and starter definitions for scheduled tasks."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.agent_harness import pin_recurring_skill
from infrastructure.scheduling.scheduler.credentials import (
    resolve_slack_credentials,
    resolve_slack_default_chat_id,
    resolve_telegram_credentials,
    resolve_telegram_default_chat_id,
)
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_CREATED_BY_PARAM,
    LOOP_DESCRIPTION_PARAM,
    LOOP_GROUP_ID_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_REPORT_ARGS_PARAM,
    LOOP_REPORT_PARAM,
    LOOP_SLACK_CHAT_ID_PARAM,
    LOOP_SLUG_PARAM,
    LOOP_SOURCE_PARAM,
    LOOP_TELEGRAM_CHAT_ID_PARAM,
    LOOP_TIME_PARAM,
)
from infrastructure.scheduling.scheduler.operation_log import (
    record_scheduler_loop_operation,
    record_scheduler_task_operation,
)
from infrastructure.scheduling.scheduler.runner import compute_next_run
from infrastructure.scheduling.scheduler.storage import (
    add_task,
    list_tasks,
    remove_task,
    update_task,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind

_ONBOARDING_LOOP_SOURCE = "onboarding"
_MANUAL_LOOP_SOURCE = "manual"
_MANUAL_LOOP_CREATED_BY = "interactive_shell"
_LEGACY_MORNING_REPORT_PROMPT = (
    "Summarize the reliability picture for the last 24 hours: notable "
    "alerts, error spikes, and anything on-call should know this morning."
)
_TIME_RE = re.compile(
    r"^(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)?$",
    re.IGNORECASE,
)

SUPPORTED_MANUAL_LOOP_CHANNELS: tuple[Provider, ...] = (
    Provider.TELEGRAM,
    Provider.SLACK,
    Provider.INTERACTIVE_SHELL,
)


@dataclass(frozen=True, slots=True)
class StarterLoop:
    """A draft loop created during onboarding as a practical template."""

    slug: str
    name: str
    description: str
    kind: TaskKind
    cron: str
    timezone: str
    window_hours: int
    prompt: str = ""
    skill_name: str = ""


@dataclass(frozen=True, slots=True)
class ManualLoop:
    """The persisted task plus display metadata for a newly created loop."""

    task: ScheduledTask
    channels: tuple[Provider, ...]
    next_run: str | None


@dataclass(frozen=True, slots=True)
class LoopSummary:
    """Display-ready scheduled loop details."""

    id: str
    task_ids: tuple[str, ...]
    name: str
    description: str
    prompt: str
    kind: TaskKind
    cron: str
    timezone: str
    provider: Provider
    chat_id: str
    channels: tuple[str, ...]
    enabled: bool
    window_hours: int
    last_run: str | None
    next_run: str | None
    schedule_error: str = ""

    @property
    def status(self) -> str:
        return "active" if self.enabled else "draft"

    @property
    def time(self) -> str:
        """Human-readable execution time for simple cron schedules."""
        return loop_time_label(self.cron)


@dataclass(frozen=True, slots=True)
class LoopMutation:
    """Result of changing one loop group."""

    summary: LoopSummary
    task_ids: tuple[str, ...]

    @property
    def task_count(self) -> int:
        return len(self.task_ids)


STARTER_LOOPS: tuple[StarterLoop, ...] = (
    StarterLoop(
        slug="morning-report",
        name="Morning report",
        description="Weekday weather and news briefing.",
        kind=TaskKind.RECURRING_SKILL,
        cron="0 8 * * 1-5",
        timezone="UTC",
        window_hours=24,
        skill_name="morning-report",
    ),
    StarterLoop(
        slug="weekly-alert-audit",
        name="Weekly alert audit",
        description="Monday review of noisy and actionable alert patterns.",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * 1",
        timezone="UTC",
        window_hours=168,
        prompt=(
            "Review alert activity over the last week: which alerts were noisy, "
            "which were actionable, and what tuning would reduce noise."
        ),
    ),
    StarterLoop(
        slug="pr-sweep",
        name="PR sweep",
        description="Weekday standup digest for stale, blocked, or ready pull requests.",
        kind=TaskKind.GITHUB_PR_SWEEP,
        cron="0 9 * * 1-5",
        timezone="UTC",
        window_hours=24,
    ),
)

_KIND_LABELS: dict[TaskKind, str] = {
    TaskKind.MANUAL_LOOP: "Manual loop",
    TaskKind.SENTRY_MORNING_DIGEST: "Sentry morning digest",
    TaskKind.SENTRY_UPTIME_WATCH: "Sentry uptime watch",
    TaskKind.GITHUB_PR_SWEEP: "GitHub PR sweep",
    TaskKind.RECURRING_SKILL: "Recurring skill",
}


def loop_name(task: ScheduledTask) -> str:
    """Return the human label for a scheduled task."""
    if task.name.strip():
        return task.name.strip()
    slug = task.params.get(LOOP_SLUG_PARAM, "").strip()
    if slug:
        return slug.replace("-", " ").title()
    return _KIND_LABELS.get(task.kind, task.kind.value.replace("_", " ").title())


def loop_description(task: ScheduledTask) -> str:
    """Return optional loop description metadata."""
    return task.params.get(LOOP_DESCRIPTION_PARAM, "").strip()


def loop_channels(task: ScheduledTask) -> tuple[Provider, ...]:
    """Return the providers a loop task delivers to."""
    raw = task.params.get(LOOP_CHANNELS_PARAM, "").strip()
    if not raw:
        return (task.provider,)

    providers: list[Provider] = []
    seen: set[Provider] = set()
    for part in raw.split(","):
        value = part.strip().lower()
        if not value:
            continue
        try:
            provider = Provider(value)
        except ValueError:
            continue
        if provider not in seen:
            providers.append(provider)
            seen.add(provider)
    return tuple(providers) or (task.provider,)


def loop_time_label(cron: str) -> str:
    """Return ``HH:MM`` when ``cron`` has a fixed hour/minute schedule."""
    parts = cron.split()
    if len(parts) != 5:
        return ""
    minute, hour = parts[0], parts[1]
    if not (minute.isdigit() and hour.isdigit()):
        return ""
    return f"{int(hour):02d}:{int(minute):02d}"


def cron_for_time(time_text: str, *, weekdays: bool = False) -> str:
    """Build a daily or weekday cron expression from a human time string."""
    hour, minute = parse_loop_time(time_text)
    day_of_week = "1-5" if weekdays else "*"
    return f"{minute} {hour} * * {day_of_week}"


def parse_loop_time(time_text: str) -> tuple[int, int]:
    """Parse ``HH:MM``/``8am`` style input into ``(hour, minute)``."""
    text = time_text.strip().lower()
    match = _TIME_RE.fullmatch(text)
    if match is None:
        raise ValueError("time must look like 08:30, 8, 8am, or 8:30pm")

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    meridiem = match.group("meridiem")

    if minute > 59:
        raise ValueError("minute must be between 00 and 59")
    if meridiem:
        if hour < 1 or hour > 12:
            raise ValueError("12-hour time must use an hour between 1 and 12")
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif hour > 23:
        raise ValueError("24-hour time must use an hour between 0 and 23")
    return hour, minute


def default_loop_channels() -> tuple[Provider, ...]:
    """Return every currently reachable default loop delivery channel."""
    channels: list[Provider] = []
    telegram_creds = resolve_telegram_credentials({})
    if telegram_creds.get("bot_token") and resolve_telegram_default_chat_id({}):
        channels.append(Provider.TELEGRAM)

    slack_creds = resolve_slack_credentials({})
    if slack_creds.get("webhook_url") or (
        slack_creds.get("access_token") and resolve_slack_default_chat_id({})
    ):
        channels.append(Provider.SLACK)

    channels.append(Provider.INTERACTIVE_SHELL)
    return tuple(channels)


def normalize_loop_channels(
    requested_channels: Sequence[str] | None,
    *,
    telegram_chat_id: str = "",
    slack_chat_id: str = "",
) -> tuple[Provider, ...]:
    """Normalize requested loop channels, defaulting to all reachable handles."""
    if not requested_channels:
        channels = default_loop_channels()
    else:
        expanded = _expand_channel_values(requested_channels)
        if "all" in expanded:
            channels = default_loop_channels()
        else:
            channels = tuple(_provider_from_channel(value) for value in expanded)

    channels = _dedupe_providers(channels)
    for channel in channels:
        _validate_channel_ready(
            channel,
            telegram_chat_id=telegram_chat_id,
            slack_chat_id=slack_chat_id,
        )
    return channels


def create_manual_loop(
    *,
    name: str,
    prompt: str,
    time_text: str = "",
    timezone: str = "UTC",
    weekdays: bool = False,
    cron: str = "",
    channels: Sequence[str] | None = None,
    telegram_chat_id: str = "",
    slack_chat_id: str = "",
    window_hours: int = 24,
    store_path: Path | None = None,
    report: str = "",
    report_args: Mapping[str, str] | None = None,
) -> ManualLoop:
    """Create an active recurring prompt loop.

    ``report`` names a deterministic report builder the runner uses instead
    of a model turn; ``prompt`` then documents the loop and is the fallback
    when the builder is not installed.
    """
    loop_prompt = prompt.strip()
    if not loop_prompt:
        raise ValueError("prompt is required")
    if window_hours < 1:
        raise ValueError("window_hours must be at least 1")

    cron_expr = " ".join(cron.split())
    if not cron_expr:
        if not time_text.strip():
            raise ValueError("time is required unless --cron is provided")
        cron_expr = cron_for_time(time_text, weekdays=weekdays)

    channel_providers = normalize_loop_channels(
        channels,
        telegram_chat_id=telegram_chat_id,
        slack_chat_id=slack_chat_id,
    )
    loop_id = uuid.uuid4().hex[:12]
    params = {
        LOOP_GROUP_ID_PARAM: loop_id,
        LOOP_SOURCE_PARAM: _MANUAL_LOOP_SOURCE,
        LOOP_CREATED_BY_PARAM: _MANUAL_LOOP_CREATED_BY,
        LOOP_PROMPT_PARAM: loop_prompt,
        LOOP_DESCRIPTION_PARAM: _description_from_prompt(loop_prompt),
        LOOP_CHANNELS_PARAM: ",".join(provider.value for provider in channel_providers),
    }
    time_label = loop_time_label(cron_expr)
    if time_label:
        params[LOOP_TIME_PARAM] = time_label
    if report.strip():
        params[LOOP_REPORT_PARAM] = report.strip()
        params[LOOP_REPORT_ARGS_PARAM] = json.dumps(dict(report_args or {}), sort_keys=True)
    if telegram_chat_id.strip():
        params[LOOP_TELEGRAM_CHAT_ID_PARAM] = telegram_chat_id.strip()
    if slack_chat_id.strip():
        params[LOOP_SLACK_CHAT_ID_PARAM] = slack_chat_id.strip()

    task = ScheduledTask(
        id=loop_id,
        name=name.strip() or _name_from_prompt(loop_prompt),
        kind=TaskKind.MANUAL_LOOP,
        cron=cron_expr,
        timezone=timezone.strip() or "UTC",
        provider=Provider.INTERACTIVE_SHELL,
        window_hours=window_hours,
        enabled=True,
        params=params,
    )
    task.next_run = compute_next_run(task)
    stored_task = add_task(task, store_path)
    deduplicated = stored_task.id != task.id
    record_scheduler_task_operation(
        "scheduled_loop_reused" if deduplicated else "scheduled_loop_created",
        stored_task,
        channels=channel_providers,
        extra={
            "requested_task_id": task.id,
            "deduplicated": deduplicated,
        },
    )
    return ManualLoop(
        task=stored_task,
        channels=channel_providers,
        next_run=stored_task.next_run or task.next_run,
    )


def summarize_loop(
    task: ScheduledTask,
    *,
    now: datetime | None = None,
) -> LoopSummary:
    """Build a display summary for one scheduled task."""
    return _summarize_group((task,), now=now)


def list_loop_summaries(
    *,
    include_disabled: bool = True,
    store_path: Path | None = None,
    now: datetime | None = None,
) -> list[LoopSummary]:
    """Return configured loops, sorted with active loops first."""
    task_groups: dict[str, list[ScheduledTask]] = {}
    for task in list_tasks(store_path):
        if not include_disabled and not task.enabled:
            continue
        task_groups.setdefault(_loop_group_id(task), []).append(task)

    summaries = [_summarize_group(tuple(tasks), now=now) for tasks in task_groups.values()]
    return sorted(
        summaries,
        key=lambda loop: (not loop.enabled, loop.name.casefold(), loop.id),
    )


def resolve_loop_summary(
    identifier: str,
    *,
    include_disabled: bool = True,
    store_path: Path | None = None,
    now: datetime | None = None,
) -> tuple[LoopSummary | None, str]:
    """Resolve a loop by id prefix, task id prefix, or exact name."""
    query = identifier.strip()
    if not query:
        return None, "loop id or name is required"

    lowered = query.casefold()
    matches = [
        loop
        for loop in list_loop_summaries(
            include_disabled=include_disabled,
            store_path=store_path,
            now=now,
        )
        if loop.id.startswith(query)
        or lowered == loop.name.casefold()
        or any(task_id.startswith(query) for task_id in loop.task_ids)
    ]
    if not matches:
        return None, f"loop {query!r} not found"
    if len(matches) > 1:
        options = ", ".join(loop.id for loop in matches[:5])
        return None, f"loop id {query!r} is ambiguous: {options}"
    return matches[0], ""


def set_loop_enabled(
    identifier: str,
    *,
    enabled: bool,
    store_path: Path | None = None,
    now: datetime | None = None,
) -> tuple[LoopMutation | None, str]:
    """Enable or disable all tasks that belong to one loop."""
    loop, error = resolve_loop_summary(identifier, store_path=store_path, now=now)
    if loop is None:
        return None, error

    task_ids = set(loop.task_ids)
    updated: list[str] = []
    for task in list_tasks(store_path):
        if task.id not in task_ids:
            continue
        task.enabled = enabled
        if enabled:
            try:
                task.next_run = compute_next_run(task, now)
            except ValueError as exc:
                return None, str(exc)
        else:
            task.next_run = None
        if not update_task(task, store_path):
            return None, f"loop {loop.id!r} could not be updated"
        updated.append(task.id)

    if not updated:
        return None, f"loop {loop.id!r} has no persisted tasks"
    refreshed_loop, _ = resolve_loop_summary(loop.id, store_path=store_path, now=now)
    mutation = LoopMutation(summary=refreshed_loop or loop, task_ids=tuple(updated))
    record_scheduler_loop_operation(
        "scheduled_loop_started" if enabled else "scheduled_loop_stopped",
        mutation.summary,
        task_ids=mutation.task_ids,
    )
    return mutation, ""


def delete_loop(
    identifier: str,
    *,
    store_path: Path | None = None,
    now: datetime | None = None,
) -> tuple[LoopMutation | None, str]:
    """Delete all tasks that belong to one loop."""
    loop, error = resolve_loop_summary(identifier, store_path=store_path, now=now)
    if loop is None:
        return None, error

    removed: list[str] = []
    for task_id in loop.task_ids:
        if not remove_task(task_id, store_path):
            return None, f"loop task {task_id!r} could not be removed"
        removed.append(task_id)
    mutation = LoopMutation(summary=loop, task_ids=tuple(removed))
    record_scheduler_loop_operation(
        "scheduled_loop_deleted",
        loop,
        task_ids=mutation.task_ids,
    )
    return mutation, ""


def seed_starter_loops(store_path: Path | None = None) -> list[ScheduledTask]:
    """Create onboarding starter loops once.

    Starter loops are drafts: they show concrete cron examples without silently
    posting to a user's chat destination. Users can create an active loop with
    ``/loops add`` or the natural-language schedule flow.
    """
    existing_by_slug = {
        task.params.get(LOOP_SLUG_PARAM, "").strip(): task
        for task in list_tasks(store_path)
        if task.params.get(LOOP_SOURCE_PARAM) == _ONBOARDING_LOOP_SOURCE
    }
    added: list[ScheduledTask] = []
    for starter in STARTER_LOOPS:
        existing = existing_by_slug.get(starter.slug)
        if existing is not None:
            upgraded = _upgrade_legacy_skill_starter(existing, starter, store_path=store_path)
            if upgraded is not None:
                added.append(upgraded)
            continue
        params = {
            LOOP_SLUG_PARAM: starter.slug,
            LOOP_SOURCE_PARAM: _ONBOARDING_LOOP_SOURCE,
            LOOP_DESCRIPTION_PARAM: starter.description,
            LOOP_CHANNELS_PARAM: Provider.INTERACTIVE_SHELL.value,
        }
        if starter.prompt:
            params[LOOP_PROMPT_PARAM] = starter.prompt
        skill_name = ""
        skill_revision = ""
        if starter.skill_name:
            skill_name, skill_revision = pin_recurring_skill(starter.skill_name)
        task = ScheduledTask(
            name=starter.name,
            kind=starter.kind,
            cron=starter.cron,
            timezone=starter.timezone,
            provider=Provider.INTERACTIVE_SHELL,
            window_hours=starter.window_hours,
            enabled=False,
            params=params,
            skill_name=skill_name,
            skill_revision=skill_revision,
        )
        stored_task = add_task(task, store_path)
        record_scheduler_task_operation("scheduled_loop_seeded", stored_task)
        added.append(stored_task)
    return added


def _upgrade_legacy_skill_starter(
    task: ScheduledTask,
    starter: StarterLoop,
    *,
    store_path: Path | None,
) -> ScheduledTask | None:
    """Upgrade only an untouched disabled onboarding prompt to its named skill."""
    if (
        not starter.skill_name
        or task.enabled
        or task.kind is not TaskKind.MANUAL_LOOP
        or task.params.get(LOOP_SOURCE_PARAM) != _ONBOARDING_LOOP_SOURCE
        or task.params.get(LOOP_PROMPT_PARAM) != _LEGACY_MORNING_REPORT_PROMPT
        or task.skill_name
    ):
        return None

    skill_name, skill_revision = pin_recurring_skill(starter.skill_name)
    task.kind = TaskKind.RECURRING_SKILL
    task.skill_name = skill_name
    task.skill_revision = skill_revision
    task.skill_inputs = {}
    task.params.pop(LOOP_PROMPT_PARAM, None)
    task.params[LOOP_DESCRIPTION_PARAM] = starter.description
    if not update_task(task, store_path):
        return None
    record_scheduler_task_operation(
        "scheduled_loop_upgraded",
        task,
        extra={"from_kind": TaskKind.MANUAL_LOOP.value},
    )
    return task


def _summarize_group(
    tasks: tuple[ScheduledTask, ...],
    *,
    now: datetime | None,
) -> LoopSummary:
    representative = sorted(tasks, key=lambda task: (task.created_at, task.id))[0]
    next_runs: list[str] = []
    schedule_errors: list[str] = []
    for task in tasks:
        try:
            computed_next_run = compute_next_run(task, now)
        except ValueError as exc:
            schedule_errors.append(str(exc))
        else:
            if computed_next_run:
                next_runs.append(computed_next_run)

    channels: list[str] = []
    seen_channels: set[str] = set()
    for task in tasks:
        for provider in loop_channels(task):
            if provider.value not in seen_channels:
                channels.append(provider.value)
                seen_channels.add(provider.value)

    last_runs = [task.last_run for task in tasks if task.last_run]
    return LoopSummary(
        id=_loop_group_id(representative),
        task_ids=tuple(task.id for task in tasks),
        name=loop_name(representative),
        description=loop_description(representative),
        prompt=representative.params.get(LOOP_PROMPT_PARAM, "").strip(),
        kind=representative.kind,
        cron=representative.cron,
        timezone=representative.timezone,
        provider=representative.provider,
        chat_id=representative.chat_id,
        channels=tuple(channels) or (representative.provider.value,),
        enabled=any(task.enabled for task in tasks),
        window_hours=representative.window_hours,
        last_run=max(last_runs) if last_runs else None,
        next_run=min(next_runs) if next_runs else representative.next_run,
        schedule_error="; ".join(schedule_errors),
    )


def _loop_group_id(task: ScheduledTask) -> str:
    return task.params.get(LOOP_GROUP_ID_PARAM, "").strip() or task.id


def _description_from_prompt(prompt: str) -> str:
    compact = " ".join(prompt.split())
    return compact[:120]


def _name_from_prompt(prompt: str) -> str:
    compact = " ".join(prompt.split())
    if not compact:
        return "Manual loop"
    return compact[:48].rstrip(" .,")


def _expand_channel_values(values: Sequence[str]) -> tuple[str, ...]:
    expanded: list[str] = []
    for value in values:
        expanded.extend(part.strip().lower() for part in value.split(",") if part.strip())
    return tuple(expanded)


def _provider_from_channel(value: str) -> Provider:
    normalized = value.replace("-", "_")
    aliases = {
        "interactive": Provider.INTERACTIVE_SHELL,
        "shell": Provider.INTERACTIVE_SHELL,
        "local": Provider.INTERACTIVE_SHELL,
        Provider.INTERACTIVE_SHELL.value: Provider.INTERACTIVE_SHELL,
    }
    provider = aliases.get(normalized)
    if provider is not None:
        return provider
    try:
        provider = Provider(normalized)
    except ValueError as exc:
        allowed = ", ".join(provider.value for provider in SUPPORTED_MANUAL_LOOP_CHANNELS)
        raise ValueError(f"unsupported loop channel {value!r}; use all, {allowed}") from exc
    if provider not in SUPPORTED_MANUAL_LOOP_CHANNELS:
        allowed = ", ".join(provider.value for provider in SUPPORTED_MANUAL_LOOP_CHANNELS)
        raise ValueError(f"unsupported loop channel {value!r}; use all, {allowed}")
    return provider


def _dedupe_providers(providers: Sequence[Provider]) -> tuple[Provider, ...]:
    deduped: list[Provider] = []
    seen: set[Provider] = set()
    for provider in providers:
        if provider not in seen:
            deduped.append(provider)
            seen.add(provider)
    return tuple(deduped)


def _validate_channel_ready(
    provider: Provider,
    *,
    telegram_chat_id: str,
    slack_chat_id: str,
) -> None:
    if provider == Provider.INTERACTIVE_SHELL:
        return
    if provider == Provider.TELEGRAM:
        bot_token = resolve_telegram_credentials({}).get("bot_token", "")
        chat_id = telegram_chat_id.strip() or resolve_telegram_default_chat_id({})
        if bot_token and chat_id:
            return
        raise ValueError(
            "telegram loop delivery needs TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_DEFAULT_CHAT_ID, or pass --telegram-chat-id"
        )
    if provider == Provider.SLACK:
        creds = resolve_slack_credentials({})
        if creds.get("webhook_url") or (
            creds.get("access_token")
            and (slack_chat_id.strip() or resolve_slack_default_chat_id({}))
        ):
            return
        raise ValueError(
            "slack loop delivery needs SLACK_WEBHOOK_URL, or SLACK_BOT_TOKEN with "
            "SLACK_DEFAULT_CHAT_ID / --slack-chat-id"
        )


__all__ = [
    "LoopMutation",
    "LoopSummary",
    "ManualLoop",
    "STARTER_LOOPS",
    "SUPPORTED_MANUAL_LOOP_CHANNELS",
    "StarterLoop",
    "create_manual_loop",
    "cron_for_time",
    "default_loop_channels",
    "delete_loop",
    "list_loop_summaries",
    "loop_channels",
    "loop_description",
    "loop_name",
    "loop_time_label",
    "normalize_loop_channels",
    "parse_loop_time",
    "resolve_loop_summary",
    "seed_starter_loops",
    "set_loop_enabled",
    "summarize_loop",
]
