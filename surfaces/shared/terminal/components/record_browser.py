"""Read-only search over the same records used by static terminal lists."""

from __future__ import annotations

from prompt_toolkit.application import Application
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from infrastructure.terminal.theme import HIGHLIGHT, INPUT_SURFACE, TEXT
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import InputChoice, InputField, build_input_form
from surfaces.shared.terminal.components.rendering import print_repl_renderable
from surfaces.shared.terminal.tables.records import RecordTable

_DEFAULT_FRAME_WIDTH = 96
_FRAME_CHROME_WIDTH = 4


def _record_frame_width(table: RecordTable) -> int:
    """Keep compact lists bounded while giving dense columns the width they need."""
    return max(_DEFAULT_FRAME_WIDTH, table.summary_content_width + _FRAME_CHROME_WIDTH)


def build_record_browser(table: RecordTable, *, title: str) -> Application[None]:
    """Search summary cells and metadata without accepting or mutating a record."""
    choices = tuple(
        InputChoice(
            str(index),
            row.cells[0].plain,
            search_text=" ".join(line.plain for line in (*row.metadata, *row.details)),
            record=row,
        )
        for index, row in enumerate(table.rows)
    )

    def fields(_values: dict[str, str]) -> list[InputField]:
        return [
            InputField(
                "record",
                table.title,
                description=table.subtitle,
                searchable=True,
                record_choices=True,
                record_columns=table.columns,
                choices=choices,
            )
        ]

    return build_input_form(
        {"record": ""},
        title=title,
        heading=table.title,
        submit_label="Close",
        fields=fields,
        validate=lambda _values: None,
        initial_field="record",
        browse_only=True,
        max_width=_record_frame_width(table),
    )


def print_record_list(
    console: Console, table: RecordTable, *, title: str, browse: bool = False
) -> None:
    """Browse interactively, or render the complete static list when non-interactive."""
    if browse and table.rows:
        run_command_input(build_record_browser(table, title=title))
        return
    renderable = (
        Panel(
            table,
            title=Text(title),
            box=box.SQUARE,
            width=_record_frame_width(table),
            padding=(1, 0),
            style=f"{TEXT} on {INPUT_SURFACE}",
            border_style=str(HIGHLIGHT),
        )
        if browse
        else table
    )
    print_repl_renderable(console, renderable)
