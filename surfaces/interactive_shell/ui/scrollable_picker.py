"""Keyboard navigation for bounded inline terminal pickers."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence

from surfaces.shared.terminal.components.choice_menu import (
    enter_inline_menu,
    erase_menu_lines,
    leave_inline_menu,
    read_menu_action,
    repl_tty_interactive,
)


def _visible_row_count(max_visible_rows: int, chrome_rows: int) -> int:
    rows = shutil.get_terminal_size(fallback=(80, 24)).lines
    return min(max_visible_rows, max(1, rows - chrome_rows - 1))


def choose_scrollable[T](
    items: Sequence[T],
    *,
    draw: Callable[..., int],
    max_visible_rows: int,
    chrome_rows: int,
    selected: int = 0,
) -> T | None:
    """Run a bounded inline picker and return its selected item."""
    if not items or not repl_tty_interactive():
        return None
    top = 0
    drawn_height = 0
    enter_inline_menu()
    try:
        while True:
            visible_rows = _visible_row_count(max_visible_rows, chrome_rows)
            top = min(top, max(0, len(items) - visible_rows))
            if selected < top:
                top = selected
            elif selected >= top + visible_rows:
                top = selected - visible_rows + 1
            drawn_height = draw(
                items,
                selected=selected,
                top=top,
                visible_rows=visible_rows,
                erase_lines=drawn_height,
            )
            action = read_menu_action()
            if action == "up":
                selected = (selected - 1) % len(items)
            elif action == "down":
                selected = (selected + 1) % len(items)
            elif action == "enter":
                return items[selected]
            elif action in ("cancel", "eof"):
                return None
    finally:
        erase_menu_lines(drawn_height, delete=True)
        leave_inline_menu()
