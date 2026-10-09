"""Terminal-native work-item editing with selectable detail rows."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Box, Frame, TextArea

from core.domain.work_items import WORK_ITEM_PRIORITIES
from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components.command_input import command_input_style

_FIELDS = ("title", "priority", "project", "owner", "due")


def build_work_form(
    options: dict[str, str], *, projects: Sequence[str] = (), today: date | None = None
) -> Application[dict[str, str] | None]:
    """Collect a title, then edit selected details; persist only after explicit Create."""
    current = {name: options.get(name, "normal" if name == "priority" else "") for name in _FIELDS}
    current["priority"] = current["priority"].lower()
    local_day = today or datetime.now().astimezone().date()
    project_names = sorted({name for name in projects if name}, key=str.casefold)
    row = 1
    field = "title"
    mode = "text"
    choice_index = 0
    error = ""
    editor = TextArea(height=1, multiline=False, prompt="› ")

    def choices() -> list[tuple[str | None, str]]:
        if field == "priority":
            return [(value, value.capitalize()) for value in WORK_ITEM_PRIORITIES]
        if field == "due":
            return [
                ("", "None"),
                (local_day.isoformat(), f"Today · {local_day.isoformat()}"),
                ((local_day + timedelta(days=1)).isoformat(), "Tomorrow"),
                (None, f"Custom · {current['due']}" if current["due"] else "Enter a date…"),
            ]
        query = editor.text.strip().casefold()
        names = sorted(
            set(project_names) | ({current["project"]} if current["project"] else set()),
            key=str.casefold,
        )
        return [
            *((("", "None"),) if not query else ()),
            *((name, name) for name in names if query in name.casefold()),
            (None, "Enter a new project…"),
        ]

    def reset_choice(_buffer: object) -> None:
        nonlocal choice_index
        if mode == "choice" and field == "project":
            choice_index = 0

    editor.buffer.on_text_changed += reset_choice

    def summary_rows() -> StyleAndTextTuples:
        labels = [
            f"{name.capitalize():<10} {strip_terminal_controls(current[name]) or 'Not set'}"
            for name in _FIELDS
        ]
        labels.extend(("Create work item", "Cancel"))
        return [
            (
                "class:selected" if index == row else "",
                ("\n" if index == len(_FIELDS) else "")
                + ("› " if index == row else "  ")
                + label
                + "\n",
            )
            for index, label in enumerate(labels)
        ]

    def choice_rows() -> StyleAndTextTuples:
        return [
            (
                "class:selected" if index == choice_index else "",
                ("› " if index == choice_index else "  ") + strip_terminal_controls(label) + "\n",
            )
            for index, (_, label) in enumerate(choices())
        ]

    summary = FormattedTextControl(
        summary_rows,
        focusable=True,
        get_cursor_position=lambda: Point(0, row + (row >= len(_FIELDS))),
    )
    selection = FormattedTextControl(
        choice_rows, focusable=True, get_cursor_position=lambda: Point(0, choice_index)
    )

    def show_summary() -> None:
        nonlocal mode, error
        mode, error = "summary", ""
        app.layout.focus(summary)

    def open_field(name: str) -> None:
        nonlocal field, mode, choice_index, error
        field, error = name, ""
        if name in {"priority", "project", "due"}:
            mode = "choice"
            editor.text = ""
            choice_index = next(
                (index for index, (value, _) in enumerate(choices()) if value == current[name]),
                len(choices()) - 1 if current[name] else 0,
            )
            app.layout.focus(editor if name == "project" else selection)
        else:
            mode = "text"
            editor.text = current[name]
            editor.buffer.cursor_position = len(editor.text)
            app.layout.focus(editor)

    def accept_text() -> None:
        nonlocal error
        value = editor.text.strip()
        if field == "title" and not value:
            error = "Enter a title."
            return
        if field == "due" and value:
            try:
                datetime.fromisoformat(value)
            except ValueError:
                error = "Use YYYY-MM-DD or an ISO datetime."
                return
        current[field] = value
        show_summary()

    def create() -> None:
        nonlocal error
        if not current["title"]:
            open_field("title")
            error = "Enter a title."
        elif current["priority"] not in WORK_ITEM_PRIORITIES:
            open_field("priority")
            error = "Choose a valid priority."
        else:
            app.exit(result=dict(current))

    keys = KeyBindings()

    @keys.add("enter")
    def accept(event: KeyPressEvent) -> None:
        nonlocal mode
        if mode == "text":
            accept_text()
        elif mode == "choice":
            value, _ = choices()[choice_index]
            if value is None:
                draft = editor.text if field == "project" else current[field]
                mode = "text"
                editor.text = draft
                editor.buffer.cursor_position = len(draft)
                app.layout.focus(editor)
            else:
                current[field] = value
                show_summary()
        elif row < len(_FIELDS):
            open_field(_FIELDS[row])
        elif row == len(_FIELDS):
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
            row = (row + step) % (len(_FIELDS) + 2)
        else:
            choice_index = (choice_index + step) % len(choices())

    @keys.add("escape")
    def back(event: KeyPressEvent) -> None:
        if mode == "summary" or not current["title"]:
            event.app.exit(result=None)
        else:
            show_summary()

    @keys.add("c-c")
    @keys.add("c-d")
    def cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    @keys.add("c-s", filter=Condition(lambda: mode == "summary"))
    def save(_event: KeyPressEvent) -> None:
        create()

    def heading() -> str:
        if mode == "summary":
            return "Choose a detail to edit, or create the item."
        if mode == "text":
            return (
                "Title (required)"
                if field == "title"
                else f"{field.capitalize()} · leave empty to unset"
            )
        return f"Choose {field}" + (" · type to filter" if field == "project" else "")

    def hint() -> str:
        if mode == "text":
            return "Enter save field · Esc back · Ctrl-C cancel"
        if mode == "choice":
            return "↑↓ choose · Enter select · Esc back"
        return "↑↓/Tab move · Enter edit/select · Ctrl-S create · Esc cancel"

    app: Application[dict[str, str] | None] = Application(
        layout=Layout(
            Frame(
                Box(
                    HSplit(
                        [
                            Window(FormattedTextControl(heading), height=1),
                            Window(height=Dimension(min=0, preferred=1, max=1)),
                            ConditionalContainer(
                                Window(summary, height=8),
                                filter=Condition(lambda: mode == "summary"),
                            ),
                            ConditionalContainer(
                                editor,
                                filter=Condition(
                                    lambda: (
                                        mode == "text" or (mode == "choice" and field == "project")
                                    )
                                ),
                            ),
                            ConditionalContainer(
                                Window(selection, height=6),
                                filter=Condition(lambda: mode == "choice"),
                            ),
                            ConditionalContainer(
                                Window(
                                    FormattedTextControl(lambda: [("class:error", error)]), height=1
                                ),
                                filter=Condition(lambda: bool(error)),
                            ),
                            Window(height=Dimension(min=0, preferred=1, max=1)),
                            Window(
                                FormattedTextControl(lambda: [("class:hint", hint())]), height=1
                            ),
                        ]
                    ),
                    padding=0,
                    padding_left=2,
                    padding_right=2,
                    padding_top=Dimension(min=0, preferred=1, max=1),
                    padding_bottom=Dimension(min=0, preferred=1, max=1),
                ),
                title="/work add",
            ),
            focused_element=editor,
        ),
        key_bindings=keys,
        style=command_input_style(),
        full_screen=False,
        erase_when_done=True,
    )
    return app
