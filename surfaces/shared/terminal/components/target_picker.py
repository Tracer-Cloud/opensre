"""Record-target selection composed from the shared input form."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from prompt_toolkit.application import Application
from rich.text import Text

from surfaces.shared.terminal.components.input_form import InputChoice, InputField, build_input_form
from surfaces.shared.terminal.tables.records import (
    RecordColumn,
    RecordRow,
    RecordTable,
    record_frame_width,
)


@dataclass(frozen=True)
class TargetChoice:
    """Stable identity, compact scan text, and details for one selectable record."""

    value: str
    title: str
    summary: str = ""
    details: tuple[str, ...] = ()
    search_text: str = ""
    summary_style: str = "description"
    compact_summary: str = ""
    compact_details: tuple[str, ...] = ()
    minimal_details: tuple[str, ...] = ()
    cells: tuple[Text, ...] = ()


def build_target_picker(
    choices: Sequence[TargetChoice],
    *,
    title: str,
    heading: str,
    action_label: str,
    confirmation: str = "",
    compact_confirmation: str = "",
    columns: tuple[RecordColumn, ...] = (),
    fit_columns: bool = False,
) -> Application[str | None]:
    """Collect a stable target with the shared form's selection and submission flow."""
    by_id = {choice.value: choice for choice in choices}
    records = tuple(
        RecordRow((Text(choice.title, style="bold"), *choice.cells)) for choice in choices
    )
    options = tuple(
        InputChoice(
            choice.value,
            choice.title,
            summary=choice.summary,
            details=choice.details,
            search_text=choice.search_text,
            summary_style=choice.summary_style,
            compact_summary=choice.compact_summary,
            compact_details=choice.compact_details,
            minimal_details=choice.minimal_details,
            record=record if columns else None,
        )
        for choice, record in zip(choices, records, strict=True)
    )

    def fields(values: dict[str, str]) -> list[InputField]:
        selected = by_id.get(values["target"])
        return [
            InputField(
                "target",
                heading,
                required=True,
                searchable=True,
                record_choices=True,
                record_columns=columns,
                choices=options,
                summary=selected.title if selected is not None else None,
            )
        ]

    return build_input_form(
        {"target": ""},
        title=title,
        heading=f"Review · {action_label}",
        submit_label=action_label,
        fields=fields,
        validate=lambda values: values["target"],
        initial_field="target",
        submit_on_choice=not confirmation,
        focus_submit_on_choice=bool(confirmation),
        confirmation=confirmation,
        compact_confirmation=compact_confirmation,
        max_width=(
            record_frame_width(RecordTable("", columns, records)) if columns and fit_columns else 96
        ),
    )
