"""Read-only, scrollable details using the inline menu terminal lifecycle."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

import infrastructure.terminal.theme as ui_theme
from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components import choice_menu
from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width


def build_detail_panel(
    title: str,
    fields: Sequence[tuple[str, str]],
    *,
    note: str = "",
    width: int,
    height: int,
    offset: int = 0,
) -> tuple[list[str], int, int]:
    """Return padded rows, clamped scroll offset, and the final scroll offset."""
    width, height = max(1, width), max(1, height)
    framed = width >= 12 and height >= 5
    content_width = max(1, width - 4) if framed else width
    console = Console(width=content_width)
    content: list[str] = []
    label_width = max((prompt_text_width(label) for label, _ in fields), default=0)
    for label, value in fields:
        label = strip_terminal_controls(label)
        value = strip_terminal_controls(value)
        if content_width >= label_width + 24:
            text = label + " " * (label_width - prompt_text_width(label) + 2) + value
        else:
            text = f"{label}\n{value}"
        content.extend(
            line.plain for line in Text(text).wrap(console, content_width, overflow="fold")
        )
        content.append("")
    if note:
        content.extend(
            line.plain
            for line in Text(strip_terminal_controls(note)).wrap(
                console, content_width, overflow="fold"
            )
        )
    elif content:
        content.pop()
    capacity = max(1, height - 4) if framed else height
    limit = max(0, len(content) - capacity)
    offset = min(max(0, offset), limit)
    visible = content[offset : offset + capacity]
    frame, reset = ui_theme.PROMPT_FRAME_ANSI, ui_theme.ANSI_RESET

    def pad(text: str, size: int) -> str:
        text = clip_prompt_text(text, size)
        return text + " " * (size - prompt_text_width(text))

    if not framed:
        return [pad(line, width) for line in visible], offset, limit
    inner = width - 2
    heading = f" {title} "
    rows = [f"{frame}╭{pad(heading, inner)}╮{reset}"]
    rows.extend(
        f"{frame}│{reset}{ui_theme.TEXT_ANSI} {pad(line, content_width)} {reset}{frame}│{reset}"
        for line in visible
    )
    hint = "Enter / Esc back"
    if limit:
        hint = "↑↓ scroll · " + hint
    rows.extend(
        [
            f"{frame}├{'─' * inner}┤{reset}",
            f"{frame}│{reset}{ui_theme.DIM_ANSI}{pad(' ' + hint, inner)}{reset}{frame}│{reset}",
            f"{frame}╰{'─' * inner}╯{reset}",
        ]
    )
    return rows, offset, limit


def repl_show_details(*, title: str, fields: Sequence[tuple[str, str]], note: str = "") -> None:
    """Show transient details; Enter or Esc returns without changing configuration."""
    if not choice_menu.repl_tty_interactive():
        return
    choice_menu._clear_prompt_toolkit_paint()
    drain_stale_cpr_bytes()
    choice_menu.hide_terminal_cursor()
    offset = 0
    try:
        while True:
            width = choice_menu._menu_paint_width()
            lines, offset, limit = build_detail_panel(
                title,
                fields,
                note=note,
                width=width,
                height=choice_menu._viewport_rows() - 1,
                offset=offset,
            )
            for line in lines:
                choice_menu.write_menu_line(line)
            sys.stdout.flush()
            action = choice_menu._read_action()
            columns = max(1, choice_menu._cols())
            choice_menu._erase_menu_block(
                len(lines) * ((width + columns - 1) // columns), delete=True
            )
            sys.stdout.flush()
            if action in ("enter", "cancel"):
                return
            if action == "up":
                offset = max(0, offset - 1)
            elif action == "down":
                offset = min(limit, offset + 1)
    finally:
        choice_menu.leave_inline_menu()
