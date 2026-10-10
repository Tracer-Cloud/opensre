"""Editable cron task details with explicit, validated creation."""

from __future__ import annotations

import click
from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Box, Frame, TextArea

from infrastructure.safety.terminal_output import strip_terminal_controls
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
from surfaces.shared.terminal.components.command_input import command_input_style
from surfaces.shared.terminal.prompt_layout import clip_prompt_text


def build_cron_form(values: dict[str, str]) -> Application[list[str] | None]:
    """Edit a draft without side effects; Create is the only successful exit."""
    current = dict(values)
    options = cron_options()
    row = 0
    field = "kind"
    mode = "summary"
    expanded = False
    error = ""
    choice_index = 0
    editor = TextArea(height=1, multiline=False, prompt="› ")

    def names() -> list[str]:
        primary = review_fields(current)
        if not expanded:
            return primary
        primary_set = set(primary)
        return [name for name in visible_cron_fields(current) if name not in primary_set]

    def action_labels() -> tuple[str, ...]:
        return ("Back to schedule",) if expanded else ("More options…", "Create schedule", "Cancel")

    def choices() -> list[str]:
        option = options[field]
        if option.is_flag:
            return ["False", "True"]
        if isinstance(option.type, click.Choice):
            values = [str(value) for value in option.type.choices if value != "work_item_reminder"]
            return values if field in required_cron_fields(current) else [""] + values
        return []

    def marked(index: int, text: str) -> tuple[str, str]:
        style = "class:selected" if index == row else ""
        return style, ("› " if index == row else "  ") + text + "\n"

    def summary_rows() -> StyleAndTextTuples:
        width = max(1, min(96, app.output.get_size().columns) - 8)
        # Full values are editable; keep the summary short enough to navigate on narrow screens.
        return [
            marked(
                index,
                clip_prompt_text(f"{field_label(name):<14}{summary_value(name, current)}", width),
            )
            for index, name in enumerate(names())
        ]

    summary = FormattedTextControl(
        summary_rows,
        focusable=True,
        get_cursor_position=lambda: Point(0, min(row, max(0, len(names()) - 1))),
    )
    actions = FormattedTextControl(
        lambda: [
            marked(len(names()) + index, label) for index, label in enumerate(action_labels())
        ],
        focusable=True,
        get_cursor_position=lambda: Point(0, max(0, row - len(names()))),
    )

    def choice_rows() -> StyleAndTextTuples:
        return [
            (
                "class:selected" if index == choice_index else "",
                ("› " if index == choice_index else "  ") + choice_label(field, value) + "\n",
            )
            for index, value in enumerate(choices())
        ]

    selection = FormattedTextControl(
        choice_rows, focusable=True, get_cursor_position=lambda: Point(0, choice_index)
    )

    def focus_row() -> None:
        app.layout.focus(summary if row < len(names()) else actions)

    def show_summary() -> None:
        nonlocal mode, row
        mode = "summary"
        fields = names()
        row = fields.index(field) if field in fields else 0
        focus_row()

    def open_field(name: str) -> None:
        nonlocal field, expanded, mode, choice_index
        field = name
        expanded = name not in review_fields(current)
        candidates = choices()
        if candidates:
            mode = "choice"
            selected = current[field]
            option_type = options[field].type
            if isinstance(option_type, click.Choice) and not option_type.case_sensitive:
                selected = selected.casefold()
                candidates = [value.casefold() for value in candidates]
            choice_index = candidates.index(selected) if selected in candidates else 0
            app.layout.focus(selection)
        else:
            mode = "text"
            editor.text = current[field]
            editor.buffer.cursor_position = len(editor.text)
            app.layout.focus(editor)

    def create() -> None:
        nonlocal error
        try:
            args = validate_cron_draft(current)
        except (click.ClickException, ValueError) as exc:
            error = strip_terminal_controls(
                exc.format_message() if isinstance(exc, click.ClickException) else str(exc)
            )
            missing = missing_cron_fields(current)
            param = exc.param.name if isinstance(exc, click.BadParameter) and exc.param else None
            target = param or (missing[0] if missing else None)
            if target is not None and target in options:
                open_field(target)
            return
        app.exit(result=args)

    keys = KeyBindings()

    @keys.add("enter")
    def accept(event: KeyPressEvent) -> None:
        nonlocal expanded, error, row
        if mode != "summary":
            value = choices()[choice_index] if mode == "choice" else editor.text.strip()
            if not value and field in required_cron_fields(current):
                error = f"{field_label(field)} is required."
                return
            current[field] = value
            error = ""
            show_summary()
            return
        fields = names()
        if row < len(fields):
            open_field(fields[row])
        elif row == len(fields):
            expanded = not expanded
            row = 0
            error = ""
            focus_row()
        elif row == len(fields) + 1:
            create()
        else:
            event.app.exit(result=None)

    @keys.add("up", filter=Condition(lambda: mode != "text"))
    @keys.add("down", filter=Condition(lambda: mode != "text"))
    @keys.add("tab", filter=Condition(lambda: mode != "text"))
    @keys.add("s-tab", filter=Condition(lambda: mode != "text"))
    def move(event: KeyPressEvent) -> None:
        nonlocal row, choice_index
        step = -1 if event.key_sequence[0].key in {"up", "s-tab"} else 1
        if mode == "summary":
            row = (row + step) % (len(names()) + len(action_labels()))
            focus_row()
        else:
            choice_index = (choice_index + step) % len(choices())

    @keys.add("escape")
    def back(event: KeyPressEvent) -> None:
        nonlocal expanded, row, error
        if mode != "summary":
            show_summary()
        elif expanded:
            expanded = False
            row = len(names())
            error = ""
            focus_row()
        else:
            event.app.exit(result=None)

    @keys.add("c-c")
    @keys.add("c-d")
    def cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    @keys.add("c-s", filter=Condition(lambda: mode == "summary" and not expanded))
    def save(_event: KeyPressEvent) -> None:
        create()

    def heading() -> StyleAndTextTuples:
        if mode == "summary":
            return [("bold", "More options" if expanded else "New schedule · save without running")]
        return [
            ("bold", field_label(field)),
            ("class:description", f" · {field_status(field, current)}"),
        ]

    def hint() -> str:
        if mode == "summary":
            return "↑↓/Tab move · Enter select · Esc " + ("back" if expanded else "cancel")
        return (
            "↑↓ choose · Enter select · Esc back"
            if mode == "choice"
            else "Enter save field · Esc back"
        )

    def spacer() -> Window:
        return Window(height=Dimension(min=0, preferred=1, max=1))

    review = HSplit(
        [
            Window(
                summary,
                height=lambda: Dimension(min=1, max=min(8, len(names()))),
                dont_extend_height=True,
            ),
            spacer(),
            Window(actions, height=lambda: len(action_labels())),
        ]
    )
    app: Application[list[str] | None] = Application(
        layout=Layout(
            Frame(
                Box(
                    HSplit(
                        [
                            Window(FormattedTextControl(heading), height=1),
                            spacer(),
                            ConditionalContainer(
                                review, filter=Condition(lambda: mode == "summary")
                            ),
                            ConditionalContainer(
                                Window(
                                    FormattedTextControl(
                                        lambda: [("class:description", field_help(field, current))]
                                    ),
                                    wrap_lines=True,
                                    height=Dimension(min=1, max=4),
                                    dont_extend_height=True,
                                ),
                                filter=Condition(lambda: mode != "summary"),
                            ),
                            ConditionalContainer(editor, filter=Condition(lambda: mode == "text")),
                            ConditionalContainer(
                                Window(
                                    selection,
                                    height=Dimension(min=1, max=6),
                                    dont_extend_height=True,
                                ),
                                filter=Condition(lambda: mode == "choice"),
                            ),
                            ConditionalContainer(
                                Window(
                                    FormattedTextControl(lambda: [("class:error", error)]),
                                    height=Dimension(min=1, max=4),
                                    wrap_lines=True,
                                    dont_extend_height=True,
                                ),
                                filter=Condition(lambda: bool(error)),
                            ),
                            spacer(),
                            Window(
                                FormattedTextControl(lambda: [("class:hint", hint())]),
                                wrap_lines=True,
                                dont_extend_height=True,
                            ),
                        ]
                    ),
                    padding=0,
                    padding_left=2,
                    padding_right=2,
                ),
                title="/cron add",
                width=Dimension(max=96),
            ),
            focused_element=summary,
        ),
        key_bindings=keys,
        style=command_input_style(),
        full_screen=False,
        erase_when_done=True,
    )
    missing = set(missing_cron_fields(current))
    first_missing = next((name for name in review_fields(current) if name in missing), None)
    if first_missing is not None:
        open_field(first_missing)
    return app
