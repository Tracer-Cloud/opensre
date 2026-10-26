"""Cron field definitions for the shared editable command form."""

from __future__ import annotations

from functools import cache

import click
from prompt_toolkit.application import Application

from infrastructure.scheduling.scheduler.credentials import requires_explicit_chat_id
from surfaces.interactive_shell.ui.cron_input.arguments import (
    cron_options,
    missing_cron_fields,
    required_cron_fields,
    validate_cron_draft,
    visible_cron_fields,
)
from surfaces.interactive_shell.ui.cron_input.presentation import (
    choice_label,
    field_help,
    field_label,
    field_status,
    review_fields,
    summary_value,
)
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
    build_input_form,
)


def _fields(values: dict[str, str], required: set[str]) -> list[InputField]:
    primary = review_fields(values, required=required)
    primary_set = set(primary)
    names = primary + [name for name in visible_cron_fields(values) if name not in primary_set]
    result = []
    for name in names:
        option = cron_options()[name]
        choices = []
        if option.is_flag:
            choices = ["False", "True"]
        elif isinstance(option.type, click.Choice):
            choices = [str(value) for value in option.type.choices if value != "work_item_reminder"]
            if name not in required:
                choices.insert(0, "")
        result.append(
            InputField(
                name=name,
                label=field_label(name),
                description=field_help(name, values),
                status=field_status(name, values, required=name in required),
                required=name in required,
                choices=tuple(InputChoice(value, choice_label(name, value)) for value in choices),
                case_sensitive=not isinstance(option.type, click.Choice)
                or option.type.case_sensitive,
                summary=summary_value(name, values, required=name in required),
                advanced=name not in primary_set,
            )
        )
    return result


def _validate(values: dict[str, str]) -> list[str]:
    try:
        return validate_cron_draft(values)
    except (click.ClickException, ValueError) as exc:
        message = exc.format_message() if isinstance(exc, click.ClickException) else str(exc)
        missing = missing_cron_fields(values)
        param = exc.param.name if isinstance(exc, click.BadParameter) and exc.param else None
        raise InputFormError(message, field=param or (missing[0] if missing else None)) from exc


def build_cron_form(values: dict[str, str]) -> Application[list[str] | None]:
    """Edit a draft without side effects; Create is the only successful exit."""

    @cache
    def destination_required(provider: str) -> bool:
        return requires_explicit_chat_id(provider)

    def fields(current: dict[str, str]) -> list[InputField]:
        required = required_cron_fields(
            current, explicit_destination=destination_required(current["provider"].lower())
        )
        return _fields(current, set(required))

    return build_input_form(
        values,
        title="/cron add",
        heading="New schedule · save only",
        submit_label="Create schedule",
        fields=fields,
        validate=_validate,
    )
