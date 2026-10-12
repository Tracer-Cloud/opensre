"""Searchable picker for choosing a stored memory to show."""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Box, Frame, TextArea

from core.domain.memory import MemoryRecord
from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components.command_input import command_input_style
from surfaces.shared.terminal.prompt_layout import clip_prompt_text


def build_memory_picker(records: Sequence[MemoryRecord]) -> Application[str | None]:
    """Filter memories by name or description; Enter returns the chosen slug.

    Cancelling (Esc, Ctrl-C) exits with ``None`` and the caller must change nothing.
    """
    index = 0

    def matches() -> list[MemoryRecord]:
        query = editor.text.strip().casefold()
        if not query:
            return list(records)
        return [
            record
            for record in records
            if query in record.slug.casefold() or query in record.description.casefold()
        ]

    def reset_index(_buffer: object) -> None:
        nonlocal index
        index = 0

    editor = TextArea(height=1, multiline=False, prompt="› ")
    editor.buffer.on_text_changed += reset_index

    def rows() -> StyleAndTextTuples:
        found = matches()
        if not found:
            return [("class:description", "no memories match that search\n")]
        width = max(1, min(96, app.output.get_size().columns) - 8)
        return [
            (
                "class:selected" if position == index else "",
                ("› " if position == index else "  ")
                + strip_terminal_controls(
                    clip_prompt_text(
                        f"{record.slug} · {record.description}"
                        if record.description
                        else record.slug,
                        width,
                    )
                )
                + "\n",
            )
            for position, record in enumerate(found)
        ]

    keys = KeyBindings()

    @keys.add("up")
    @keys.add("down")
    def move(event: KeyPressEvent) -> None:
        nonlocal index
        count = len(matches())
        if count:
            step = -1 if event.key_sequence[0].key == "up" else 1
            index = (index + step) % count

    @keys.add("enter")
    def choose(_event: KeyPressEvent) -> None:
        found = matches()
        if found:
            app.exit(result=found[index].slug)

    @keys.add("escape")
    @keys.add("c-c")
    @keys.add("c-d")
    def cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    def list_height() -> Dimension:
        # Reserve borders, heading, filter editor, gap and the shortcut line.
        count = max(1, min(8, len(matches())))
        available = max(1, app.output.get_size().rows - 6)
        return Dimension(min=min(count, available), max=count)

    app: Application[str | None] = Application(
        layout=Layout(
            Frame(
                Box(
                    HSplit(
                        [
                            Window(
                                FormattedTextControl(lambda: [("bold", "Show memory")]),
                                height=1,
                            ),
                            editor,
                            Window(
                                FormattedTextControl(
                                    rows,
                                    focusable=True,
                                    get_cursor_position=lambda: Point(0, index),
                                ),
                                height=list_height,
                                dont_extend_height=True,
                            ),
                            Window(height=1),
                            Window(
                                FormattedTextControl(
                                    lambda: [
                                        (
                                            "class:hint",
                                            "Type to filter · ↑↓ choose · Enter show · Esc cancel",
                                        )
                                    ]
                                ),
                                wrap_lines=True,
                                dont_extend_height=True,
                            ),
                        ]
                    ),
                    padding=0,
                    padding_left=2,
                    padding_right=2,
                ),
                title="/memory show",
                width=Dimension(max=96),
            ),
            focused_element=editor,
        ),
        key_bindings=keys,
        style=command_input_style(),
        full_screen=False,
        erase_when_done=True,
    )
    return app


__all__ = ["build_memory_picker"]
