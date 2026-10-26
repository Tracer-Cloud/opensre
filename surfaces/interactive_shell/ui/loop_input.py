"""Editable fields for creating a recurring prompt loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from prompt_toolkit.application import Application

from infrastructure.scheduling.scheduler.cron_expression import cap_cron_at_most_hourly
from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODES
from infrastructure.scheduling.scheduler.loops import (
    normalize_loop_channels,
    parse_loop_time,
)
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
    build_input_form,
)

_DAILY = "daily"
_WEEKDAYS = "weekdays"
_CRON = "cron"
_TRUE = "True"
_FALSE = "False"


@dataclass(slots=True)
class LoopInput:
    """Raw ``/loops add`` values retained while the user repairs a draft."""

    name: str = ""
    prompt: str = ""
    time_text: str = ""
    timezone: str = "UTC"
    cron: str = ""
    cron_selected: bool = False
    weekdays: bool = False
    run_now: bool = False
    channels: list[str] = field(default_factory=list)
    telegram_chat_id: str = ""
    slack_chat_id: str = ""
    window_hours: str = "24"
    mode: str = ""


def _form_values(draft: LoopInput) -> dict[str, str]:
    schedule_type = (
        _CRON if draft.cron or draft.cron_selected else (_WEEKDAYS if draft.weekdays else _DAILY)
    )
    return {
        "name": draft.name,
        "prompt": draft.prompt,
        "schedule_type": schedule_type,
        "schedule": draft.cron if draft.cron else draft.time_text,
        "timezone": draft.timezone or "UTC",
        "mode": draft.mode or "report",
        "delivery": ",".join(draft.channels),
        "telegram_chat_id": draft.telegram_chat_id,
        "slack_chat_id": draft.slack_chat_id,
        "window_hours": draft.window_hours,
        "run_now": _TRUE if draft.run_now else _FALSE,
    }


def _required(value: str, *, label: str, field_name: str) -> None:
    if not value.strip():
        raise InputFormError(f"Enter {label}.", field=field_name)


def _validate(values: dict[str, str]) -> LoopInput:
    _required(values["name"], label="a loop name", field_name="name")
    _required(values["prompt"], label="what the loop should do", field_name="prompt")
    _required(values["schedule"], label="a time or cron schedule", field_name="schedule")

    timezone = values["timezone"].strip() or "UTC"
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise InputFormError("Enter a valid IANA timezone, such as UTC.", field="timezone") from exc

    schedule_type = values["schedule_type"]
    schedule = values["schedule"]
    try:
        if schedule_type == _CRON:
            cron = cap_cron_at_most_hourly(schedule, timezone)
            time_text = ""
        else:
            parse_loop_time(schedule)
            cron = ""
            time_text = schedule
    except ValueError as exc:
        raise InputFormError(str(exc), field="schedule") from exc

    try:
        window_hours = int(values["window_hours"])
    except ValueError as exc:
        raise InputFormError(
            "Lookback must be a whole number of hours.", field="window_hours"
        ) from exc
    if window_hours < 1:
        raise InputFormError("Lookback must be at least 1 hour.", field="window_hours")

    mode = values["mode"].strip()
    if mode not in LOOP_MODES:
        raise InputFormError(f"Choose one of {', '.join(LOOP_MODES)}.", field="mode")

    channels = [part.strip() for part in values["delivery"].split(",") if part.strip()]
    try:
        normalize_loop_channels(
            channels or None,
            telegram_chat_id=values["telegram_chat_id"],
            slack_chat_id=values["slack_chat_id"],
        )
    except ValueError as exc:
        raise InputFormError(str(exc), field="delivery") from exc

    return LoopInput(
        name=values["name"].strip(),
        prompt=values["prompt"].strip(),
        time_text=time_text,
        timezone=timezone,
        cron=cron,
        cron_selected=schedule_type == _CRON,
        weekdays=schedule_type == _WEEKDAYS,
        run_now=values["run_now"] == _TRUE,
        channels=channels,
        telegram_chat_id=values["telegram_chat_id"].strip(),
        slack_chat_id=values["slack_chat_id"].strip(),
        window_hours=str(window_hours),
        mode=mode,
    )


def _delivery_choices(value: str) -> tuple[InputChoice, ...]:
    known_values = {"", "interactive_shell", "slack", "telegram", "all"}
    supplied = () if value in known_values else (InputChoice(value, value),)
    return (
        InputChoice("", "All reachable channels · default"),
        InputChoice("interactive_shell", "This terminal"),
        InputChoice("slack", "Slack"),
        InputChoice("telegram", "Telegram"),
        InputChoice("all", "All reachable channels"),
        *supplied,
        InputChoice(None, "Enter multiple channels…"),
    )


def build_loop_form(draft: LoopInput) -> Application[LoopInput | None]:
    """Edit a loop draft; creation remains behind the explicit submit action."""
    current = _form_values(draft)

    def fields(values: dict[str, str]) -> list[InputField]:
        schedule_type = values["schedule_type"]
        schedule_label = "Cron schedule" if schedule_type == _CRON else "Time"
        schedule_help = (
            "Use a 5-field cron expression, such as 0 8 * * mon-fri. "
            "Runs are limited to at most once an hour."
            if schedule_type == _CRON
            else "Use 08:30, 8, 8am, or 8:30pm."
        )
        selected_channels = {
            part.strip().lower() for part in values["delivery"].split(",") if part.strip()
        }
        result = [
            InputField(
                "name",
                "Name",
                description="A short, readable name for this loop.",
                status="required",
                required=True,
                cancel_on_back_if_empty=True,
            ),
            InputField(
                "prompt",
                "Prompt",
                description="What should OpenSRE do each time this loop runs?",
                status="required",
                required=True,
            ),
            InputField(
                "schedule_type",
                "Frequency",
                description="Choose a daily time, weekdays only, or a cron expression.",
                status="default Daily",
                choices=(
                    InputChoice(_DAILY, "Daily · default"),
                    InputChoice(_WEEKDAYS, "Weekdays"),
                    InputChoice(_CRON, "Cron expression"),
                ),
                summary={
                    _DAILY: "Daily",
                    _WEEKDAYS: "Weekdays",
                    _CRON: "Cron expression",
                }.get(schedule_type, schedule_type),
            ),
            InputField(
                "schedule",
                schedule_label,
                description=schedule_help,
                status="required",
                required=True,
                summary=(
                    f"{values['schedule']} · {values['timezone']}"
                    if values["schedule"]
                    else "Required"
                ),
            ),
            InputField(
                "timezone",
                "Timezone",
                description="IANA timezone used to interpret the schedule, such as UTC or Europe/London.",
                status="default UTC",
                advanced=True,
            ),
            InputField(
                "mode",
                "Mode",
                description="Report returns a result; Agent may act with tools named in the prompt.",
                status="default Report",
                choices=(InputChoice("report", "Report"), InputChoice("agent", "Agent")),
                advanced=True,
            ),
            InputField(
                "delivery",
                "Delivery",
                description="Choose a channel or enter a comma-separated list.",
                status="default All reachable channels",
                choices=_delivery_choices(values["delivery"]),
                searchable=True,
                advanced=True,
            ),
        ]
        if "telegram" in selected_channels or values["telegram_chat_id"]:
            result.append(
                InputField(
                    "telegram_chat_id",
                    "Telegram chat",
                    description="Optional chat ID; leave empty to use the configured default.",
                    advanced=True,
                )
            )
        if "slack" in selected_channels or values["slack_chat_id"]:
            result.append(
                InputField(
                    "slack_chat_id",
                    "Slack channel",
                    description="Optional channel ID; leave empty to use the configured default.",
                    advanced=True,
                )
            )
        result.extend(
            (
                InputField(
                    "window_hours",
                    "Lookback",
                    description="Hours of recent context available to each run.",
                    status="default 24 hours",
                    summary=f"{values['window_hours']} hours",
                    advanced=True,
                ),
                InputField(
                    "run_now",
                    "Run now",
                    description="Run once immediately after creation. Off unless explicitly enabled.",
                    status="default Off",
                    choices=(InputChoice(_FALSE, "Off"), InputChoice(_TRUE, "On")),
                    summary="On" if values["run_now"] == _TRUE else "Off",
                    advanced=values["run_now"] != _TRUE,
                ),
            )
        )
        return result

    return build_input_form(
        current,
        title="/loops add",
        heading="New recurring loop · review before creation",
        submit_label="Create loop",
        fields=fields,
        validate=_validate,
    )


__all__ = ["LoopInput", "build_loop_form"]
