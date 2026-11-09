"""Purpose-first terminal browser for registered tools."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from shutil import get_terminal_size

from prompt_toolkit.output import create_output
from rich.console import Console
from rich.text import Text

from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme
from surfaces.shared.terminal.components.choice_menu import (
    enter_inline_menu,
    leave_inline_menu,
    repl_tty_interactive,
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


def _name_width(width: int) -> int:
    return min(34, (width - 5) // 2) if width >= 60 else width - 4


def _tool_row(entry: ToolCatalogEntry, *, selected: bool, expanded: bool, width: int) -> str:
    marker = "▾" if expanded else "›" if selected else " "
    name_width = _name_width(width)
    name = clip_prompt_text(entry.name, name_width)
    name += " " * (name_width - prompt_text_width(name))
    prefix = f"  {marker} {name}"
    summary = ""
    if width >= 60:
        description = (
            " ".join(strip_terminal_controls(entry.description, keep_whitespace=True).split())
            or "No description provided."
        )
        summary = clip_prompt_text(description.partition(". ")[0], width - name_width - 5)
        if summary.endswith("…") and " " in summary:
            summary = summary[:-1].rsplit(" ", 1)[0] + "…"
        summary = " " + summary
    if selected:
        row = prefix + summary
        row += " " * max(0, width - prompt_text_width(row))
        return _styled(row, ui_theme.MENU_SELECTION_ROW_ANSI, width)
    return (
        f"{ui_theme.TEXT_ANSI}{prefix}{ui_theme.ANSI_RESET}"
        f"{ui_theme.SECONDARY_ANSI}{summary}{ui_theme.ANSI_RESET}"
    )


def _preview_lines(entry: ToolCatalogEntry, width: int, height: int) -> list[str]:
    rows = [_styled(f"  {entry.name}", ui_theme.HIGHLIGHT_ANSI, width)]
    if height >= 5:
        rows.append("")
    description = strip_terminal_controls(
        entry.description or "No description provided.", keep_whitespace=True
    ).replace("\t", " ")
    wrapped = Text(description).wrap(Console(width=width), width - 4, overflow="fold")
    visible = min(3, height - len(rows))
    for index, line in enumerate(wrapped[:visible]):
        value = line.plain
        if index == visible - 1 and len(wrapped) > visible:
            value = clip_prompt_text(value, width - 5).rstrip("… ") + "…"
        rows.append(_styled(f"  {value}", ui_theme.TEXT_ANSI, width))
    return rows


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
    if prompt_text_width(entry.name) > width - 2:
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

    roomy = height >= 20
    rows = [_styled("  Tools", ui_theme.PROMPT_ACCENT_ANSI, width)]
    if roomy:
        rows.append("")
    if width >= 60 and height >= 14:
        rows.append(
            _styled(f"    {'TOOL':<{_name_width(width)}} PURPOSE", ui_theme.DIM_COUNTER_ANSI, width)
        )
        rows.append(_styled("  " + "─" * (width - 2), ui_theme.DIM_COUNTER_ANSI, width))

    # Reserve the divider, footer, and spacing; details stay anchored below the list.
    spaced_footer = height >= 10
    available = height - 1 - len(rows) - 3 - int(roomy) - int(spaced_footer)
    preview_height = max(2, min(5, available // 2))
    visible = min(len(entries), 20, max(1, available - preview_height))
    if details:
        preview_height = max(preview_height, min(len(details) + 1, available - visible))
    top = max(0, min(top, len(entries) - visible, selected))
    top = max(top, selected - visible + 1)
    for index in range(top, top + visible):
        rows.append(
            _tool_row(
                entries[index],
                selected=index == selected,
                expanded=bool(details) and index == selected,
                width=width,
            )
        )
    if roomy:
        rows.append("")
    rows.append(_styled("  " + "─" * (width - 2), ui_theme.DIM_COUNTER_ANSI, width))

    detail_rows = preview_height - 1
    pages = max(1, (len(details) + detail_rows - 1) // detail_rows)
    page = min(page, pages - 1)
    if details:
        preview = [_styled(f"  {entries[selected].name}", ui_theme.HIGHLIGHT_ANSI, width)]
        preview.extend(details[page * detail_rows : (page + 1) * detail_rows])
    else:
        preview = _preview_lines(entries[selected], width, preview_height)
    rows.extend(preview)
    rows.extend([""] * (preview_height - len(preview)))
    if spaced_footer:
        rows.append("")

    position = f"{selected + 1}/{len(entries)}"
    if width >= 60:
        status = f"  ←→ {page + 1}/{pages}" if pages > 1 else ""
        action = "collapse" if details else "details"
        controls = f"  ↑↓ browse   Enter {action}   Esc close"
        hint = controls + " " * max(1, width - prompt_text_width(controls + position)) + position
    else:
        status = "↑↓ browse · Enter toggle" if details else "↑↓ browse · Enter details"
        hint = f"Esc close · {position}"
        if pages > 1:
            hint = f"Esc · ←→ {page + 1}/{pages} · {position}"
    rows.extend(_styled(text, ui_theme.DIM_COUNTER_ANSI, width) for text in (status, hint))
    return _Frame(tuple(rows), top, page, pages)


def browse_tools(entries: Sequence[ToolCatalogEntry]) -> None:
    """Keep browsing until dismissed, with one tool expanded at a time."""
    if not entries or not repl_tty_interactive():
        return
    selected = top = page = 0
    expanded = False
    detail_key: tuple[int, int] | None = None
    details: list[str] = []
    output = create_output()
    enter_inline_menu()
    try:
        # A resizable catalog must not reflow into the conversation's scrollback.
        output.enter_alternate_screen()
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
            output.cursor_goto(0, 0)
            output.erase_down()
            for row in frame.rows:
                output.write_raw(f"{row}\r\n")
            output.flush()
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
            output.quit_alternate_screen()
            output.flush()
        finally:
            leave_inline_menu()
