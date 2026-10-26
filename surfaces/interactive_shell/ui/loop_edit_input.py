"""Prefilled loop edits on the shared command-input form."""

from __future__ import annotations

from collections.abc import Mapping

from prompt_toolkit.application import Application

from config.constants.scheduler import WEEKDAY_CRON_FIELD
from infrastructure.scheduling.scheduler.cron_expression import build_cron_trigger
from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODE_REPORT
from infrastructure.scheduling.scheduler.loop_edit import validate_loop_changes
from infrastructure.scheduling.scheduler.loops import LoopSummary, cron_for_time
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
    build_input_form,
)


def loop_edit_values(loop: LoopSummary) -> dict[str, str]:
    """Represent only exact daily/weekday schedules as time choices; keep other cron intact."""
    parts = loop.cron.split()
    simple = (
        len(parts) == 5
        and parts[0].isdigit()
        and parts[1].isdigit()
        and parts[2:4] == ["*", "*"]
        and parts[4] in {"*", WEEKDAY_CRON_FIELD}
    )
    return {
        "name": loop.name,
        "prompt": loop.prompt,
        "frequency": "weekdays"
        if simple and parts[4] == WEEKDAY_CRON_FIELD
        else ("daily" if simple else "cron"),
        "schedule": loop.time if simple else loop.cron,
        "timezone": loop.timezone,
        "mode": loop.mode or LOOP_MODE_REPORT,
        "window_hours": str(loop.window_hours),
    }


def _required(value: str, name: str) -> None:
    if not value.strip():
        raise InputFormError(f"Enter {name}.", field=name)


def build_loop_edit_form(
    loop: LoopSummary, *, prompt_editable: bool, options: Mapping[str, str] | None = None
) -> Application[dict[str, str] | None]:
    """Return only changed definition fields after explicit Save changes."""
    initial = loop_edit_values(loop)
    supplied = options or {}
    current = {**initial, **{name: value for name, value in supplied.items() if name in initial}}
    if "cron" in supplied:
        current.update(frequency="cron", schedule=supplied["cron"])
    elif "time" in supplied:
        current["schedule"] = supplied["time"]
        if "frequency" not in supplied and current["frequency"] == "cron":
            current["frequency"] = "daily"

    def validate(values: dict[str, str]) -> dict[str, str]:
        changed = {
            name: value
            for name, value in values.items()
            if name in {"name", "timezone", "window_hours"}
            or (prompt_editable and name in {"prompt", "mode"})
            if value != initial[name]
        }
        schedule_changed = any(values[name] != initial[name] for name in ("frequency", "schedule"))
        try:
            cron = (
                (
                    values["schedule"]
                    if values["frequency"] == "cron"
                    else cron_for_time(
                        values["schedule"], weekdays=values["frequency"] == "weekdays"
                    )
                )
                if schedule_changed
                else loop.cron
            )
            if schedule_changed or values["timezone"] != initial["timezone"]:
                build_cron_trigger(cron, values["timezone"])
        except ValueError as exc:
            field = "timezone" if values["timezone"] != initial["timezone"] else "schedule"
            raise InputFormError(str(exc), field=field) from exc
        if schedule_changed:
            changed["cron"] = cron
        for field_name in ("name", "prompt", "mode", "window_hours"):
            if field_name not in changed:
                continue
            try:
                validate_loop_changes({field_name: changed[field_name]})
            except ValueError as exc:
                raise InputFormError(str(exc), field=field_name) from exc
        try:
            return validate_loop_changes(changed)
        except ValueError as exc:
            raise InputFormError(str(exc)) from exc

    def fields(values: dict[str, str]) -> list[InputField]:
        result = [
            InputField(
                "name",
                "Name",
                required=True,
                description="A readable name for this loop."
                if prompt_editable
                else "This loop uses managed instructions. You can edit its name and schedule here.",
                validate=lambda value: _required(value, "name"),
            ),
        ]
        if prompt_editable:
            result.append(
                InputField(
                    "prompt",
                    "Prompt",
                    required=True,
                    description="What should OpenSRE do each time this loop runs?",
                    validate=lambda value: _required(value, "prompt"),
                )
            )
        result.extend(
            (
                InputField(
                    "frequency",
                    "Frequency",
                    description="Choose a daily time, weekdays only, or a cron expression.",
                    choices=(
                        InputChoice("daily", "Daily"),
                        InputChoice("weekdays", "Weekdays"),
                        InputChoice("cron", "Cron expression"),
                    ),
                    summary={"daily": "Daily", "weekdays": "Weekdays", "cron": "Cron expression"}[
                        values["frequency"]
                    ],
                ),
                InputField(
                    "schedule",
                    "Cron schedule" if values["frequency"] == "cron" else "Time",
                    required=True,
                    description="Enter a cron expression, such as 0 8 * * mon-fri."
                    if values["frequency"] == "cron"
                    else "Use 08:30, 8, 8am, or 8:30pm.",
                    summary=f"{values['schedule']} · {values['timezone']}",
                ),
                InputField(
                    "timezone",
                    "Timezone",
                    description="IANA timezone, such as UTC or Asia/Kolkata.",
                    advanced=True,
                ),
            )
        )
        if prompt_editable:
            result.append(
                InputField(
                    "mode",
                    "Mode",
                    description="Report returns a result; Agent may act with tools named in the prompt.",
                    choices=(InputChoice("report", "Report"), InputChoice("agent", "Agent")),
                    advanced=True,
                )
            )
        result.append(
            InputField(
                "window_hours",
                "Lookback",
                description="Hours of recent context available to each run.",
                summary=f"{values['window_hours']} hours",
                advanced=True,
            )
        )
        return result

    return build_input_form(
        current,
        title="/loops edit",
        heading="Edit recurring loop",
        submit_label="Save changes",
        fields=fields,
        validate=validate,
    )


__all__ = ["build_loop_edit_form", "loop_edit_values"]
