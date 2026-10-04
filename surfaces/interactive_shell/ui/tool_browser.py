"""Purpose-first, inline browser for registered tools."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from shutil import get_terminal_size

from rich.console import Console
from rich.text import Text

from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme
from surfaces.shared.terminal.components.choice_menu import (
    enter_inline_menu,
    erase_menu_lines,
    leave_inline_menu,
    repl_tty_interactive,
    write_menu_line,
)
from surfaces.shared.terminal.components.key_reader import read_menu_or_char
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width
from surfaces.shared.terminal.tables.tool_catalog import ToolCatalogEntry


@dataclass(frozen=True)
class _Frame:
    rows: tuple[str, ...]
    top: int
    page: int
    pages: int


def _styled(text: str, style: str, width: int) -> str:
    return f"{style}{clip_prompt_text(text, width)}{ui_theme.ANSI_RESET}"


def _tool_rows(
    entry: ToolCatalogEntry, *, selected: bool, expanded: bool, width: int
) -> tuple[str, str]:
    marker = "▾" if expanded else "›" if selected else " "
    surfaces = strip_terminal_controls(" · ".join(entry.surfaces)) if width >= 60 else ""
    name_width = max(1, width - prompt_text_width(surfaces) - 2) if surfaces else width
    name = clip_prompt_text(f"  {marker} {entry.name}", name_width)
    padding = " " * max(0, width - prompt_text_width(name + surfaces))
    style = ui_theme.prominent_menu_selection_ansi() if selected else ui_theme.HIGHLIGHT_ANSI
    heading = (
        f"{style}{name}{ui_theme.ANSI_RESET}{padding}"
        f"{ui_theme.DIM_COUNTER_ANSI}{surfaces}{ui_theme.ANSI_RESET}"
    )
    summary = " ".join(strip_terminal_controls(entry.description, keep_whitespace=True).split())
    return heading, _styled(
        f"    {summary or 'No description provided.'}", ui_theme.SECONDARY_ANSI, width
    )


def _detail_lines(entry: ToolCatalogEntry, width: int) -> list[str]:
    """Wrap literal metadata at word boundaries without truncating its contents."""
    params = "\n".join(
        f"{param.name}: {param.type_label} ({'required' if param.required else 'optional'})"
        for param in entry.parameters
    )
    fields = [
        ("surfaces", ", ".join(entry.surfaces) or "None"),
        ("parameters", params or entry.input_schema_summary),
        ("description", entry.description or "No description provided."),
    ]
    surfaces_width = prompt_text_width(" · ".join(entry.surfaces)) + 2 if width >= 60 else 0
    if prompt_text_width(entry.name) > width - surfaces_width - 4:
        fields.insert(0, ("name", entry.name))
    console = Console(width=width)
    rows: list[str] = []
    for label, value in fields:
        value = strip_terminal_controls(value, keep_whitespace=True).replace("\t", " ")
        if width < 50:
            rows.append(_styled(f"    {label}", ui_theme.DIM_COUNTER_ANSI, width))
            rows.extend(
                _styled(f"      {line.plain}", ui_theme.TEXT_ANSI, width)
                for line in Text(value).wrap(console, max(1, width - 6), overflow="fold")
            )
        else:
            for index, line in enumerate(Text(value).wrap(console, width - 16, overflow="fold")):
                prefix = f"    {label if index == 0 else '':<12}"
                rows.append(
                    f"{ui_theme.DIM_COUNTER_ANSI}{prefix}{ui_theme.ANSI_RESET}"
                    f"{ui_theme.TEXT_ANSI}{line.plain}{ui_theme.ANSI_RESET}"
                )
    return rows


def _render_frame(
    entries: Sequence[ToolCatalogEntry],
    *,
    selected: int,
    top: int,
    details: Sequence[str],
    page: int,
    width: int,
    height: int,
) -> _Frame:
    if width < 24 or height < 8:
        notice = tuple(
            _styled(text, ui_theme.DIM_COUNTER_ANSI, width)
            for text in ("Esc close", "Resize to at least 25×8", "to browse tools.")
        )
        return _Frame(notice[: max(0, height - 1)], top, 0, 1)

    chrome_rows = 4 if height >= 10 else 3
    body_rows = min(18, height - chrome_rows - 1)
    detail_rows = min(len(details), max(1, body_rows // 2)) if details else 0
    pages = max(1, (len(details) + detail_rows - 1) // detail_rows) if detail_rows else 1
    page = min(page, pages - 1)
    visible = min(len(entries), max(1, (body_rows - detail_rows) // 2))
    top = max(0, min(top, len(entries) - visible, selected))
    top = max(top, selected - visible + 1)
    end = min(len(entries), top + visible)
    position = f"{selected + 1}/{len(entries)}"
    title = f"  Tools · {len(entries)} registered" if width >= 60 else f"  Tools · {position}"
    rows = [_styled(title, ui_theme.PROMPT_ACCENT_ANSI, width)]
    if height >= 10:
        rows.append(_styled("─" * width, ui_theme.DIM_COUNTER_ANSI, width))
    for index in range(top, end):
        rows.extend(
            _tool_rows(
                entries[index],
                selected=index == selected,
                expanded=bool(details) and index == selected,
                width=width,
            )
        )
        if details and index == selected:
            start = page * detail_rows
            shown = details[start : start + detail_rows]
            rows.extend(shown)
            rows.extend([""] * (detail_rows - len(shown)))

    if width >= 60:
        more = "  ↑ earlier" if top else ""
        more += "  ↓ more" if end < len(entries) else ""
        status = f"  {position}{more}"
        if details:
            status += f"  ·  Details {page + 1}/{pages}"
        action = "collapse" if details else "details"
        paging = "   ←→ page" if pages > 1 else ""
        hint = f"  ↑↓ move   Enter {action}{paging}   Esc close"
    else:
        status = "↑↓ move · Enter toggle" if details else "↑↓ move · Enter details"
        hint = "Esc close"
        if pages > 1:
            hint += f" · ←→ page {page + 1}/{pages}"
    rows.extend(_styled(text, ui_theme.DIM_COUNTER_ANSI, width) for text in (status, hint))
    return _Frame(tuple(rows), top, page, pages)


def browse_tools(entries: Sequence[ToolCatalogEntry]) -> None:
    """Keep browsing until dismissed, with one tool expanded at a time."""
    if not entries or not repl_tty_interactive():
        return
    selected = top = page = drawn_height = 0
    expanded = False
    detail_key: tuple[int, int] | None = None
    details: list[str] = []
    enter_inline_menu()
    try:
        while True:
            size = get_terminal_size(fallback=(80, 24))
            width = max(1, size.columns - 1)
            if expanded and detail_key != (selected, width):
                details = _detail_lines(entries[selected], width)
                detail_key = (selected, width)
            frame = _render_frame(
                entries,
                selected=selected,
                top=top,
                details=details if expanded else (),
                page=page,
                width=width,
                height=size.lines,
            )
            if drawn_height:
                erase_menu_lines(drawn_height)
            drawn_height = len(frame.rows)
            for row in frame.rows:
                write_menu_line(row)
            sys.stdout.flush()
            top, page = frame.top, frame.page
            action = read_menu_or_char()
            if action in ("up", "down", "tab"):
                selected = (selected + (-1 if action == "up" else 1)) % len(entries)
                expanded = False
                page = 0
            elif action == "enter":
                expanded = not expanded
                page = 0
            elif action in ("left", "right") and expanded:
                page = max(0, min(page + (-1 if action == "left" else 1), frame.pages - 1))
            elif action in ("cancel", "eof"):
                return
    finally:
        try:
            erase_menu_lines(drawn_height, delete=True)
        finally:
            leave_inline_menu()
