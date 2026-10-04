"""Bounded arrow-key browser for registered tools."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence

from prompt_toolkit.utils import get_cwidth

from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme
from surfaces.shared.terminal.components.choice_menu import (
    enter_inline_menu,
    erase_menu_lines,
    leave_inline_menu,
    menu_columns,
    read_menu_action,
    repl_tty_interactive,
    write_menu_line,
)
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width
from surfaces.shared.terminal.tables.tool_catalog import ToolCatalogEntry

_MAX_VISIBLE_ROWS = 18


def _wrap(value: str, width: int) -> list[str]:
    """Wrap without dropping characters from tool metadata."""
    lines: list[str] = []
    for source in (
        strip_terminal_controls(value, keep_whitespace=True).replace("\t", " ").split("\n")
    ):
        line = ""
        used = 0
        for char in source:
            char_width = max(0, get_cwidth(char))
            if line and used + char_width > width:
                lines.append(line)
                line = ""
                used = 0
            line += char
            used += char_width
        lines.append(line)
    return lines


def _detail_lines(item: ToolCatalogEntry, width: int) -> list[str]:
    inner = max(1, width - 4)
    lines: list[str] = []
    for label, value in (
        ("surfaces", ", ".join(item.surfaces)),
        ("params", item.input_schema_summary),
        ("description", item.description or "-"),
    ):
        if inner <= 12:
            lines.extend(_wrap(label, inner))
            lines.extend(_wrap(value, inner))
            continue
        label_width = 12
        wrapped = _wrap(value, max(1, inner - label_width))
        lines.extend(
            f"{label:<{label_width}}{part}" if index == 0 else f"{'':<{label_width}}{part}"
            for index, part in enumerate(wrapped)
        )
    return lines


def _draw(
    items: Sequence[ToolCatalogEntry],
    *,
    selected: int,
    top: int,
    visible_rows: int,
    detail_rows: int,
    detail_page: int,
    erase_lines: int,
) -> int:
    width = menu_columns()
    if erase_lines:
        erase_menu_lines(erase_lines)
    write_menu_line()
    write_menu_line(
        f"{ui_theme.PROMPT_ACCENT_ANSI}"
        f"{clip_prompt_text(f'  Tools  ·  {len(items)} registered', width)}"
        f"{ui_theme.ANSI_RESET}"
    )
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{'─' * width}{ui_theme.ANSI_RESET}")
    end = min(len(items), top + visible_rows)
    details = _detail_lines(items[selected], width) if detail_rows else []
    shown_detail_lines = 0
    for index in range(top, end):
        item = items[index]
        marker = "▾" if detail_rows and index == selected else "›" if index == selected else " "
        label = clip_prompt_text(f"  {marker}  {item.name}", width)
        row = label + " " * max(0, width - prompt_text_width(label))
        background = ui_theme.INPUT_SURFACE_BG_ANSI if index % 2 else ui_theme.SURFACE_BG_ANSI
        style = ui_theme.prominent_menu_selection_ansi() if index == selected else background
        write_menu_line(f"{style}{row}{ui_theme.ANSI_RESET}")
        if detail_rows and index == selected:
            write_menu_line()
            first = detail_page * detail_rows
            page_lines = details[first : first + detail_rows]
            shown_detail_lines = len(page_lines)
            for line in page_lines:
                write_menu_line(
                    f"{ui_theme.TEXT_ANSI}    {clip_prompt_text(line, width - 4)}"
                    f"{ui_theme.ANSI_RESET}"
                )
            write_menu_line()
    for _ in range(visible_rows - (end - top)):
        write_menu_line()
    position = f"{selected + 1}/{len(items)}"
    page_count = (len(details) + detail_rows - 1) // detail_rows if detail_rows else 0
    action = (
        f"Enter {'next page' if detail_page + 1 < page_count else 'collapse'}"
        if detail_rows
        else "Enter details"
    )
    page = f"  ·  details {detail_page + 1}/{page_count}" if page_count > 1 else ""
    hint = f"  ↑↓ move   {action}   Esc close   ·   {position}{page}"
    write_menu_line(
        f"{ui_theme.DIM_COUNTER_ANSI}{clip_prompt_text(hint, width)}{ui_theme.ANSI_RESET}"
    )
    sys.stdout.flush()
    return visible_rows + (shown_detail_lines + 2 if detail_rows else 0) + 4


def browse_tools(items: Sequence[ToolCatalogEntry]) -> None:
    """Keep the tool list open while details expand and arrows move focus."""
    if not items or not repl_tty_interactive():
        return
    selected = top = detail_page = drawn_height = 0
    expanded = False
    enter_inline_menu()
    try:
        while True:
            terminal_rows = shutil.get_terminal_size(fallback=(80, 24)).lines
            detail_rows = min(10, max(1, (terminal_rows - 6) // 2)) if expanded else 0
            detail_length = 0
            if expanded:
                details = _detail_lines(items[selected], menu_columns())
                detail_page = min(detail_page, (len(details) - 1) // detail_rows)
                detail_length = len(
                    details[detail_page * detail_rows : (detail_page + 1) * detail_rows]
                )
            visible_rows = min(
                _MAX_VISIBLE_ROWS,
                max(1, terminal_rows - (7 + detail_length if expanded else 5)),
            )
            top = min(top, max(0, len(items) - visible_rows))
            if selected < top:
                top = selected
            elif selected >= top + visible_rows:
                top = selected - visible_rows + 1
            drawn_height = _draw(
                items,
                selected=selected,
                top=top,
                visible_rows=visible_rows,
                detail_rows=detail_rows,
                detail_page=detail_page,
                erase_lines=drawn_height,
            )
            action = read_menu_action()
            if action in ("up", "down"):
                selected = (selected + (1 if action == "down" else -1)) % len(items)
                detail_page = 0
            elif action == "enter":
                if not expanded:
                    expanded = True
                else:
                    pages = (
                        len(_detail_lines(items[selected], menu_columns())) + detail_rows - 1
                    ) // detail_rows
                    detail_page += 1
                    if detail_page >= pages:
                        expanded = False
                        detail_page = 0
            elif action in ("cancel", "eof"):
                return
    finally:
        erase_menu_lines(drawn_height, delete=True)
        leave_inline_menu()
