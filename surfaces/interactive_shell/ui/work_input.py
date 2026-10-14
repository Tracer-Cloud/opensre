"""Work-item fields and validation for the shared editable command form."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from prompt_toolkit.application import Application

from core.domain.work_items import WORK_ITEM_PRIORITIES
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
    build_input_form,
)

_FIELDS = ("title", "priority", "project", "owner", "due")


def _validate_title(value: str) -> None:
    if not value:
        raise InputFormError("Enter a title.", field="title")


def _validate_due(value: str) -> None:
    if value:
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise InputFormError("Use YYYY-MM-DD or an ISO datetime.", field="due") from exc


def _validate(values: dict[str, str]) -> dict[str, str]:
    _validate_title(values["title"])
    if values["priority"] not in WORK_ITEM_PRIORITIES:
        raise InputFormError("Choose a valid priority.", field="priority")
    return dict(values)


def build_work_form(
    options: dict[str, str], *, projects: Sequence[str] = (), today: date | None = None
) -> Application[dict[str, str] | None]:
    """Collect a title, then edit selected details; persist only after explicit Create."""
    current = {name: options.get(name, "normal" if name == "priority" else "") for name in _FIELDS}
    current["priority"] = current["priority"].lower()
    local_day = today or datetime.now().astimezone().date()
    project_names = {name for name in projects if name}

    def fields(values: dict[str, str]) -> list[InputField]:
        names = sorted(
            project_names | ({values["project"]} if values["project"] else set()), key=str.casefold
        )
        return [
            InputField(
                "title",
                "Title",
                status="required",
                required=True,
                validate=_validate_title,
                cancel_on_back_if_empty=True,
            ),
            InputField(
                "priority",
                "Priority",
                status="default Normal",
                choices=tuple(
                    InputChoice(value, value.capitalize()) for value in WORK_ITEM_PRIORITIES
                ),
            ),
            InputField(
                "project",
                "Project",
                choices=(
                    InputChoice("", "None"),
                    *(InputChoice(name, name) for name in names),
                    InputChoice(None, "Enter a new project…"),
                ),
                searchable=True,
            ),
            InputField("owner", "Owner", status="optional · leave empty to unset"),
            InputField(
                "due",
                "Due",
                description="Use YYYY-MM-DD or an ISO datetime.",
                choices=(
                    InputChoice("", "None"),
                    InputChoice(local_day.isoformat(), f"Today · {local_day.isoformat()}"),
                    InputChoice(
                        (local_day + timedelta(days=1)).isoformat(),
                        f"Tomorrow · {local_day + timedelta(days=1)}",
                    ),
                    InputChoice(
                        None, f"Custom · {values['due']}" if values["due"] else "Enter a date…"
                    ),
                ),
                validate=_validate_due,
            ),
        ]

    return build_input_form(
        current,
        title="/work add",
        heading="New work item",
        submit_label="Create work item",
        fields=fields,
        validate=_validate,
        initial_field="title",
        initial_review_field="priority",
    )
