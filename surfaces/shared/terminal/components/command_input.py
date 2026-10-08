"""Transient, keyboard-accessible command input surfaces."""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

import infrastructure.terminal.theme as theme
from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components.choice_menu import (
    enter_inline_menu,
    leave_inline_menu,
    repl_tty_interactive,
)


def command_input_style() -> Style:
    """Use the active terminal theme for input surfaces."""
    return Style.from_dict(
        {
            "": f"{theme.TEXT} bg:{theme.INPUT_SURFACE}",
            "frame.border": str(theme.HIGHLIGHT),
            "frame.label": str(theme.HIGHLIGHT),
            "selected": f"{theme.TEXT} bg:{theme.menu_selection_hex()}",
            "hint": str(theme.DIM),
            "error": str(theme.ERROR),
            "text-area": f"{theme.TEXT} bg:{theme.INPUT_SURFACE}",
        }
    )


def run_command_input[Result](app: Application[Result]) -> Result | None:
    """Run an exclusively owned input surface without leaving menu paint behind."""
    if not repl_tty_interactive():
        return None
    enter_inline_menu()
    try:
        return app.run()
    finally:
        leave_inline_menu()


def build_search_picker(
    *, title: str, choices: Sequence[tuple[str, str]], action: str
) -> Application[str | None]:
    """Build a searchable single-selection tray that preserves opaque result IDs."""
    entries = [(key, strip_terminal_controls(label)) for key, label in choices]
    selected = 0
    search = TextArea(height=1, prompt="Search: ", multiline=False)

    def matches() -> list[tuple[str, str]]:
        query = search.text.casefold().strip()
        return [entry for entry in entries if query in entry[1].casefold()]

    def reset_selection(_buffer: object) -> None:
        nonlocal selected
        selected = 0

    search.buffer.on_text_changed += reset_selection

    def rows() -> StyleAndTextTuples:
        visible = matches()
        if not visible:
            return [("class:hint", "No matching items. Edit your search or press Esc.")]
        start = max(0, selected - 3)
        fragments: StyleAndTextTuples = []
        for index in range(start, min(start + 6, len(visible))):
            style = "class:selected" if index == selected else ""
            marker = "› " if index == selected else "  "
            fragments.append((style, marker + visible[index][1] + "\n"))
        return fragments

    keys = KeyBindings()

    @keys.add("up")
    @keys.add("down")
    def move(event: KeyPressEvent) -> None:
        nonlocal selected
        count = len(matches())
        if count:
            selected = (selected + (-1 if event.key_sequence[0].key == "up" else 1)) % count

    @keys.add("enter")
    def accept(event: KeyPressEvent) -> None:
        visible = matches()
        if visible:
            event.app.exit(result=visible[selected][0])

    @keys.add("escape")
    @keys.add("c-c")
    @keys.add("c-d")
    def cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    body = HSplit(
        [
            search,
            Window(FormattedTextControl(rows), height=6, wrap_lines=False),
            Window(
                FormattedTextControl([("class:hint", f"↑↓ select · Enter {action} · Esc cancel")]),
                height=1,
            ),
        ]
    )
    return Application(
        layout=Layout(Frame(body, title=title), focused_element=search),
        key_bindings=keys,
        style=command_input_style(),
        full_screen=False,
        erase_when_done=True,
    )
