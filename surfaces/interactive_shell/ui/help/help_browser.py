"""Full-screen browser for the slash-command catalog.

Sits on the alternate screen like the ``/tools`` and ``/integrations``
browsers. The catalog is 60+ rows: painted inline it either overflows a short
terminal or reflows into the conversation it is meant to sit above.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from shutil import get_terminal_size

from prompt_toolkit.output import create_output
from rich.console import Console
from rich.text import Text

from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.ui.help.help_menu import HelpSection
from surfaces.shared.terminal.components.choice_menu import (
    enter_inline_menu,
    leave_inline_menu,
    repl_tty_interactive,
)
from surfaces.shared.terminal.components.key_reader import read_menu_or_char
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width

MIN_BROWSER_WIDTH = 32
MIN_BROWSER_HEIGHT = 12
# Sizes at which the layout earns a purpose column, column headers, and air.
_PURPOSE_WIDTH = 56
_HEADER_HEIGHT = 16
_ROOMY_HEIGHT = 22
_MAX_NAME_WIDTH = 22
_MIN_NAME_WIDTH = 10
_LABEL_WIDTH = 10
# A label row carries no command; commands index into the filtered entry list.
_LABEL_ROW = -1


@dataclass(frozen=True)
class _Entry:
    """One selectable command and the category it was listed under."""

    section: str
    command: SlashCommand


@dataclass(frozen=True)
class _Line:
    """One painted list line: a category label, or the command at ``index``."""

    index: int
    section: str = ""

    @property
    def is_label(self) -> bool:
        return self.index == _LABEL_ROW


@dataclass(frozen=True)
class _Frame:
    rows: tuple[str, ...]
    top: int
    page: int
    pages: int


def _styled(text: str, style: str, width: int) -> str:
    return f"{style}{clip_prompt_text(text, width)}{ui_theme.ANSI_RESET}"


def _flatten(sections: Sequence[HelpSection]) -> list[_Entry]:
    return [
        _Entry(section=name, command=command) for name, commands in sections for command in commands
    ]


def _match_rank(entry: _Entry, needle: str) -> int:
    """0 for a name hit, 1 for a purpose or category hit, 2 for no match."""
    if needle in entry.command.name.lower():
        return 0
    if needle in entry.command.description.lower() or needle in entry.section.lower():
        return 1
    return 2


def _filtered(entries: Sequence[_Entry], query: str) -> list[_Entry]:
    """Matches with name hits first, each command listed once.

    Deduplication matters because "Quick Access" repeats commands that also
    appear in their canonical category: grouped, the repeat reads as a
    shortcut; flattened by a filter, it reads as a duplicate.
    """
    needle = query.strip().lower()
    if not needle:
        return list(entries)
    ranked: list[list[_Entry]] = [[], []]
    seen: set[str] = set()
    for entry in entries:
        rank = _match_rank(entry, needle)
        if rank == 2 or entry.command.name in seen:
            continue
        seen.add(entry.command.name)
        ranked[rank].append(entry)
    return ranked[0] + ranked[1]


def _lines(entries: Sequence[_Entry], *, grouped: bool) -> tuple[list[_Line], list[int]]:
    """Painted list lines, plus the line each command sits on by command index."""
    lines: list[_Line] = []
    line_of: list[int] = []
    section = ""
    for index, entry in enumerate(entries):
        if grouped and entry.section != section:
            section = entry.section
            lines.append(_Line(_LABEL_ROW, section=entry.section))
        line_of.append(len(lines))
        lines.append(_Line(index))
    return lines, line_of


def _name_width(width: int) -> int:
    if width < _PURPOSE_WIDTH:
        return max(1, width - 6)
    return min(_MAX_NAME_WIDTH, max(_MIN_NAME_WIDTH, (width - 8) // 3))


def _window_top(lines: Sequence[_Line], selected_line: int, visible: int, top: int) -> int:
    """Scroll ``top`` the least that keeps ``selected_line`` on screen."""
    top = max(0, min(top, max(0, len(lines) - visible)))
    if selected_line < top:
        top = selected_line
    elif selected_line >= top + visible:
        top = selected_line - visible + 1
    # Pull in the category label directly above the window: free context, and
    # only while it cannot push the selection back off the bottom.
    if top > 0 and lines[top - 1].is_label and selected_line < top + visible - 1:
        top -= 1
    return top


def _one_line(text: str) -> str:
    return " ".join(strip_terminal_controls(text, keep_whitespace=True).split())


def _command_row(command: SlashCommand, *, selected: bool, width: int) -> str:
    name_width = _name_width(width)
    name = clip_prompt_text(command.name, name_width)
    name += " " * (name_width - prompt_text_width(name))
    prefix = f"  {'›' if selected else ' '} {name}"
    purpose = ""
    if width >= _PURPOSE_WIDTH:
        purpose = " " + clip_prompt_text(_one_line(command.description), width - name_width - 5)
    if selected:
        row = prefix + purpose
        row += " " * max(0, width - prompt_text_width(row))
        return _styled(row, ui_theme.MENU_SELECTION_ROW_ANSI, width)
    return (
        f"{ui_theme.HIGHLIGHT_ANSI}{prefix}{ui_theme.ANSI_RESET}"
        f"{ui_theme.SECONDARY_ANSI}{purpose}{ui_theme.ANSI_RESET}"
    )


def _wrapped(value: str, width: int) -> list[str]:
    text = strip_terminal_controls(value, keep_whitespace=True).replace("\t", " ")
    console = Console(width=max(1, width))
    return [line.plain for line in Text(text).wrap(console, max(1, width), overflow="fold")]


def _detail_lines(entry: _Entry, width: int) -> list[str]:
    """Preview body for one command: its purpose, then literal usage metadata."""
    fields: list[tuple[str, tuple[str, ...]]] = [
        ("", (entry.command.description or "No description provided.",))
    ]
    for label, values in (
        ("usage", entry.command.usage),
        ("examples", entry.command.examples),
        ("notes", entry.command.notes),
    ):
        if values:
            fields.append((label, values))

    body_width = max(1, width - _LABEL_WIDTH - 4)
    rows: list[str] = []
    for label, values in fields:
        for position, value in enumerate(values):
            for index, line in enumerate(_wrapped(value, body_width)):
                shown = label if position == 0 and index == 0 else ""
                prefix = f"  {shown:<{_LABEL_WIDTH}}"
                rows.append(
                    f"{ui_theme.DIM_COUNTER_ANSI}{prefix}{ui_theme.ANSI_RESET}"
                    f"{ui_theme.TEXT_ANSI}{clip_prompt_text(line, width - len(prefix))}"
                    f"{ui_theme.ANSI_RESET}"
                )
    return rows


def _too_small_frame(width: int, height: int) -> _Frame:
    notice = (
        "Esc close",
        f"Resize to at least {MIN_BROWSER_WIDTH + 1}×{MIN_BROWSER_HEIGHT}",
        "to browse commands.",
    )
    return _Frame(
        tuple(
            _styled(text, ui_theme.DIM_COUNTER_ANSI, width) for text in notice[: max(0, height - 1)]
        ),
        0,
        0,
        1,
    )


def _hint_row(*, query: str, position: str, page: int, pages: int, width: int) -> str:
    dismiss = "Esc clear" if query else "Esc close"
    # A multi-page preview must advertise ←→ at every width, or the narrow
    # layout silently hides the usage, examples and notes past page one.
    paging = f" ←→ {page + 1}/{pages}" if pages > 1 else ""
    if width < _PURPOSE_WIDTH:
        return _styled(f"  ↑↓ · Enter · {dismiss}{paging} · {position}", ui_theme.DIM_ANSI, width)
    controls = f"  ↑↓ browse   Enter run   {dismiss}   type to filter"
    if paging:
        controls += f"  {paging}"
    pad = " " * max(2, width - prompt_text_width(controls) - prompt_text_width(position) - 2)
    return _styled(f"{controls}{pad}{position}", ui_theme.DIM_ANSI, width)


def _filter_row(query: str, width: int) -> str:
    return (
        f"{ui_theme.DIM_COUNTER_ANSI}  filter  {ui_theme.ANSI_RESET}"
        f"{ui_theme.TEXT_ANSI}{clip_prompt_text(query, max(1, width - 12))}"
        f"█{ui_theme.ANSI_RESET}"
    )


def _render_frame(
    entries: Sequence[_Entry],
    *,
    selected: int,
    top: int,
    page: int,
    query: str,
    width: int,
    height: int,
) -> _Frame:
    if width < MIN_BROWSER_WIDTH or height < MIN_BROWSER_HEIGHT:
        return _too_small_frame(width, height)

    roomy = height >= _ROOMY_HEIGHT
    name_width = _name_width(width)
    rows = [_styled("  Commands", ui_theme.PROMPT_ACCENT_ANSI, width)]
    if roomy:
        rows.append("")
    if query or not entries:
        rows.append(_filter_row(query, width))
    if width >= _PURPOSE_WIDTH and height >= _HEADER_HEIGHT:
        header = f"    {'COMMAND':<{name_width}} PURPOSE"
        rows.append(_styled(header, ui_theme.DIM_COUNTER_ANSI, width))
    rule = _styled("  " + "─" * (width - 2), ui_theme.DIM_COUNTER_ANSI, width)
    rows.append(rule)

    # Reserve the closing rule, the preview, the hint row, and the optional air.
    available = max(2, height - 1 - len(rows) - 2 - int(roomy))
    preview_height = max(2, min(6, available // 2))
    list_height = max(1, available - preview_height)

    lines, line_of = _lines(entries, grouped=not query)
    top = _window_top(lines, line_of[selected], list_height, top) if entries else 0
    painted = 0
    for line in lines[top : top + list_height]:
        if line.is_label:
            rows.append(_styled(f"  {line.section}", ui_theme.BOLD_BRAND_ANSI, width))
        else:
            rows.append(
                _command_row(
                    entries[line.index].command,
                    selected=line.index == selected,
                    width=width,
                )
            )
        painted += 1
    if not entries:
        rows.append(_styled("  No command matches that filter.", ui_theme.TEXT_ANSI, width))
        painted += 1
    rows.extend([""] * max(0, list_height - painted))

    if roomy:
        rows.append("")
    rows.append(rule)

    details = _detail_lines(entries[selected], width) if entries else []
    detail_rows = max(1, preview_height - 1)
    pages = max(1, (len(details) + detail_rows - 1) // detail_rows)
    page = max(0, min(page, pages - 1))
    preview: list[str] = []
    if entries:
        crumb = f"  {entries[selected].section} › {entries[selected].command.name}"
        preview.append(_styled(crumb, ui_theme.HIGHLIGHT_ANSI, width))
        preview.extend(details[page * detail_rows : (page + 1) * detail_rows])
    rows.extend(preview[:preview_height])
    rows.extend([""] * max(0, preview_height - len(preview)))

    position = f"{selected + 1}/{len(entries)}" if entries else "0 matches"
    rows.append(_hint_row(query=query, position=position, page=page, pages=pages, width=width))
    return _Frame(tuple(rows), top, page, pages)


def browse_help_commands(sections: Sequence[HelpSection]) -> str | None:
    """Browse the catalog full screen; return the command to run, or ``None``.

    Typing filters on command name, purpose, or category; Esc clears a live
    filter before it closes the browser.
    """
    all_entries = _flatten(sections)
    if not all_entries or not repl_tty_interactive():
        return None
    query = ""
    selected = top = page = 0
    output = create_output()
    enter_inline_menu()
    try:
        output.enter_alternate_screen()
        while True:
            entries = _filtered(all_entries, query)
            selected = max(0, min(selected, len(entries) - 1))
            size = get_terminal_size(fallback=(80, 24))
            width = max(1, size.columns - 1)
            frame = _render_frame(
                entries,
                selected=selected,
                top=top,
                page=page,
                query=query,
                width=width,
                height=size.lines,
            )
            output.cursor_goto(0, 0)
            output.erase_down()
            for row in frame.rows:
                output.write_raw(f"{row}\r\n")
            output.flush()
            top, page = frame.top, frame.page

            key = read_menu_or_char(allow_chars=True)
            if key in ("cancel", "eof"):
                if not query:
                    return None
                query = ""
                selected = top = page = 0
            elif key == "backspace":
                query = query[:-1]
                selected = top = page = 0
            elif width < MIN_BROWSER_WIDTH or size.lines < MIN_BROWSER_HEIGHT:
                continue
            elif key in ("up", "down", "tab", "shift_tab"):
                if entries:
                    step = -1 if key in ("up", "shift_tab") else 1
                    selected = (selected + step) % len(entries)
                    page = 0
            elif key in ("left", "right"):
                page = max(0, min(page + (-1 if key == "left" else 1), frame.pages - 1))
            elif key == "enter":
                return entries[selected].command.name if entries else None
            elif len(key) == 1 and key.isprintable():
                query += key
                selected = top = page = 0
    finally:
        try:
            output.quit_alternate_screen()
            output.flush()
        finally:
            leave_inline_menu()


__all__ = [
    "MIN_BROWSER_HEIGHT",
    "MIN_BROWSER_WIDTH",
    "browse_help_commands",
]
