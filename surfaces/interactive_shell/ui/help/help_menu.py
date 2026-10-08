"""Help renderers for the interactive shell slash-command catalog."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.shared.terminal.components.choice_menu import (
    erase_menu_lines,
    menu_columns,
    read_menu_action,
    write_menu_line,
)
from surfaces.shared.terminal.components.rendering import (
    print_repl_table,
    repl_print,
    repl_table,
)

HelpSection = tuple[str, Sequence[SlashCommand]]
_HELP_VIEW_ROWS = 21
_HELP_CHROME_ROWS = 6
_HELP_COMPACT_CHROME_ROWS = 2
_HELP_HINT = "↑↓/j/k navigate  ·  Enter run command  ·  Space toggle details  ·  Esc/q close"
_HELP_PAGED_HINT = (
    "←→ page details  ·  ↑↓/j/k navigate  ·  Enter run  ·  Space close  ·  Esc/q close"
)
_HELP_NARROW_HINT = "↑↓ nav · Enter run · Space · Esc/q"
_HELP_NARROW_PAGED_HINT = "←→page · ↑↓nav · Enter · Space · Esc/q"


@dataclass(frozen=True)
class HelpRow:
    section: str | None = None
    command: SlashCommand | None = None
    separator: bool = False

    @property
    def selectable(self) -> bool:
        return self.command is not None


@dataclass(frozen=True)
class HelpDetailLine:
    text: str
    role: Literal["label", "value"]


@dataclass(frozen=True)
class HelpDisplayRow:
    source_index: int | None = None
    section: str | None = None
    command: SlashCommand | None = None
    detail: HelpDetailLine | None = None
    separator: bool = False


def render_help_index(console: Console, sections: Sequence[HelpSection]) -> None:
    """Render the compact non-interactive help index."""
    table = repl_table(
        title="Slash commands",
        title_style=str(ui_theme.BOLD_BRAND),
        show_header=False,
    )
    table.add_column("command", no_wrap=True, min_width=18)
    table.add_column("description", style=str(ui_theme.DIM))

    for section_name, commands in sections:
        if not commands:
            continue
        table.add_row(f"[{ui_theme.BOLD_BRAND}]{escape(section_name)}[/]", "")
        for index, command in enumerate(commands):
            table.add_row(
                f"  [{ui_theme.HIGHLIGHT}]{escape(command.name)}[/]",
                escape(command.description),
                end_section=(index == len(commands) - 1),
            )

    print_repl_table(console, table)
    repl_print(
        console,
        f"[{ui_theme.DIM}]Use[/] [bold]/help <command>[/bold] [{ui_theme.DIM}]for usage.[/]",
    )


def render_section_detail(
    console: Console,
    section_name: str,
    commands: Sequence[SlashCommand],
) -> None:
    """Render one category using the same compact description-only style."""
    table = repl_table(
        title=f"{section_name} commands",
        title_style=str(ui_theme.BOLD_BRAND),
        show_header=False,
    )
    table.add_column("command", no_wrap=True, min_width=18)
    table.add_column("description", style=str(ui_theme.DIM))
    for command in commands:
        table.add_row(
            f"[{ui_theme.HIGHLIGHT}]{escape(command.name)}[/]",
            escape(command.description),
        )
    print_repl_table(console, table)
    repl_print(
        console,
        f"[{ui_theme.DIM}]Use[/] [bold]/help <command>[/bold] [{ui_theme.DIM}]for usage.[/]",
    )


def render_command_detail(console: Console, command: SlashCommand) -> None:
    """Render detailed help for one slash command."""
    table = Table(
        title=command.name,
        title_style=str(ui_theme.BOLD_BRAND),
        show_header=False,
        box=None,
    )
    table.add_column("label", style="bold", no_wrap=True)
    table.add_column("value")
    table.add_row("description", escape(command.description))

    if command.usage:
        table.add_row("usage", "\n".join(escape(item) for item in command.usage))
    if command.examples:
        table.add_row("examples", "\n".join(escape(item) for item in command.examples))
    if command.notes:
        table.add_row("notes", "\n".join(escape(item) for item in command.notes))

    print_repl_table(console, table)


def has_help_details(command: SlashCommand) -> bool:
    """True when a command has expandable usage, examples, or notes."""
    return bool(command.usage or command.examples or command.notes)


def _flatten_help_rows(sections: Sequence[HelpSection]) -> list[HelpRow]:
    rows: list[HelpRow] = []
    for section_name, commands in sections:
        if not commands:
            continue
        if rows:
            rows.append(HelpRow(separator=True))
        rows.append(HelpRow(section=section_name))
        rows.extend(HelpRow(command=command) for command in commands)
    return rows


def _first_selectable_index(rows: Sequence[HelpRow]) -> int | None:
    for index, row in enumerate(rows):
        if row.selectable:
            return index
    return None


def _next_selectable_index(rows: Sequence[HelpRow], current: int, delta: int) -> int:
    if not rows:
        return current
    index = current
    for _ in range(len(rows)):
        index = (index + delta) % len(rows)
        if rows[index].selectable:
            return index
    return current


def _expanded_detail_lines(command: SlashCommand) -> list[HelpDetailLine]:
    lines: list[HelpDetailLine] = []
    if command.usage:
        lines.append(HelpDetailLine("usage:", "label"))
        lines.extend(HelpDetailLine(f"  {item}", "value") for item in command.usage)
    if command.examples:
        lines.append(HelpDetailLine("examples:", "label"))
        lines.extend(HelpDetailLine(f"  {item}", "value") for item in command.examples)
    if command.notes:
        lines.append(HelpDetailLine("notes:", "label"))
        lines.extend(HelpDetailLine(f"  {item}", "value") for item in command.notes)
    return lines


def _display_rows(rows: Sequence[HelpRow], expanded: int | None) -> list[HelpDisplayRow]:
    display: list[HelpDisplayRow] = []
    for index, row in enumerate(rows):
        display.append(
            HelpDisplayRow(
                source_index=index,
                section=row.section,
                command=row.command,
                separator=row.separator,
            )
        )
        if expanded == index and row.command is not None:
            display.extend(
                HelpDisplayRow(detail=line) for line in _expanded_detail_lines(row.command)
            )
    return display


def _display_index_for_source(display_rows: Sequence[HelpDisplayRow], source_index: int) -> int:
    for index, row in enumerate(display_rows):
        if row.source_index == source_index:
            return index
    return 0


def _detail_end_index(rows: Sequence[HelpDisplayRow], selected: int) -> int:
    end = selected + 1
    while end < len(rows) and rows[end].detail is not None:
        end += 1
    return end


def _expanded_viewport_height(
    rows: Sequence[HelpDisplayRow],
    selected: int,
    base_height: int,
) -> int:
    detail_end = _detail_end_index(rows, selected)
    expanded_block_height = detail_end - selected
    if expanded_block_height <= 1:
        return base_height
    return max(base_height, expanded_block_height + 2)


def _viewport_bounds(
    rows: Sequence[HelpDisplayRow],
    selected: int,
    height: int,
) -> tuple[int, int]:
    if len(rows) <= height:
        return 0, len(rows)

    before = max(0, height // 3)
    start = max(0, selected - before)
    end = start + height
    if end > len(rows):
        end = len(rows)
        start = max(0, end - height)

    detail_end = _detail_end_index(rows, selected)
    if detail_end > selected + 1 and detail_end - selected <= height:
        if selected < start:
            start = selected
            end = min(len(rows), start + height)
        if detail_end > end:
            end = detail_end
            start = max(0, end - height)

    # Keep the selected command's category header visible when it fits in the viewport.
    section_start = selected
    while section_start > 0 and rows[section_start].section is None:
        section_start -= 1
    if rows[section_start].section is not None and section_start < start:
        detail_end = _detail_end_index(rows, selected)
        distance = detail_end - section_start
        if distance <= height:
            start = section_start
            end = min(len(rows), start + height)
    return start, end


def _visible_width(text: str) -> int:
    return len(text)


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _visible_width(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def _pad(text: str, width: int) -> str:
    clipped = _clip(text, width)
    return clipped + (" " * max(0, width - _visible_width(clipped)))


def _center(text: str, width: int) -> str:
    if _visible_width(text) >= width:
        return text
    return text.center(width)


def _command_name_width(width: int) -> int:
    return min(18, max(12, width // 3))


def _left_column_width(width: int) -> int:
    return 6 + _command_name_width(width)


def _right_column_width(width: int) -> int:
    return max(0, width - _left_column_width(width) - 2)


def _render_grid_row(left: str, right: str, width: int) -> str:
    left_column = _pad(left, _left_column_width(width))
    right_column = _clip(right, _right_column_width(width))
    return _pad(f"{left_column}│ {right_column}", width)


def _render_section_row(section: str, width: int) -> str:
    left_column = _pad(section, _left_column_width(width))
    right_column = " " * _right_column_width(width)
    return (
        f"{ui_theme.BOLD_BRAND_ANSI}{left_column}{ui_theme.ANSI_RESET}"
        f"{ui_theme.DIM_COUNTER_ANSI}│ {right_column}{ui_theme.ANSI_RESET}"
    )


def _render_detail_row(detail: HelpDetailLine, width: int) -> str:
    left_column = " " * _left_column_width(width)
    right_column = _clip(detail.text, _right_column_width(width))
    right_padding = " " * max(0, _right_column_width(width) - _visible_width(right_column))
    detail_ansi = ui_theme.TEXT_ANSI if detail.role == "label" else ui_theme.DIM_COUNTER_ANSI
    return (
        f"{ui_theme.DIM_COUNTER_ANSI}{left_column}│ {ui_theme.ANSI_RESET}"
        f"{detail_ansi}{right_column}{right_padding}{ui_theme.ANSI_RESET}"
    )


def _separator_rule(width: int) -> str:
    column = _left_column_width(width)
    if column >= width:
        return "─" * width
    return f"{'─' * column}┼{'─' * max(0, width - column - 1)}"


def _render_command_row(
    command: SlashCommand,
    *,
    selected: bool,
    expanded: bool,
    width: int,
) -> str:
    marker = ">" if selected else " "
    affordance = "▾" if expanded else "▸" if has_help_details(command) else " "
    left = f" {marker} {affordance} {command.name}"
    padded = _render_grid_row(left, command.description, width)
    if selected:
        return f"{ui_theme.MENU_SELECTION_ROW_ANSI}{padded}{ui_theme.ANSI_RESET}"

    left_width = _left_column_width(width)
    right_width = _right_column_width(width)
    prefix = f" {marker} {affordance} "
    command_width = max(0, left_width - _visible_width(prefix))
    command_name = _clip(command.name, command_width)
    command_padding = " " * max(0, command_width - _visible_width(command_name))
    right_column = _clip(command.description, right_width)
    right_padding = " " * max(0, right_width - _visible_width(right_column))
    return (
        f"{ui_theme.DIM_COUNTER_ANSI}{prefix}{ui_theme.ANSI_RESET}"
        f"{ui_theme.HIGHLIGHT_ANSI}{command_name}{ui_theme.ANSI_RESET}"
        f"{ui_theme.DIM_COUNTER_ANSI}{command_padding}│ {right_column}{right_padding}"
        f"{ui_theme.ANSI_RESET}"
    )


def _render_help_row(row: HelpRow, *, selected: bool, expanded: bool, width: int) -> str:
    if row.separator:
        return f"{ui_theme.DIM_COUNTER_ANSI}{_separator_rule(width)}{ui_theme.ANSI_RESET}"
    if row.section is not None:
        return _render_section_row(row.section, width)
    if row.command is None:
        return ""
    return _render_command_row(row.command, selected=selected, expanded=expanded, width=width)


def _render_display_row(
    row: HelpDisplayRow,
    *,
    selected: bool,
    expanded: bool,
    width: int,
) -> str:
    if row.detail is not None:
        return _render_detail_row(row.detail, width)
    return _render_help_row(
        HelpRow(section=row.section, command=row.command, separator=row.separator),
        selected=selected,
        expanded=expanded,
        width=width,
    )


def _help_layout() -> Literal["regular", "compact", "tiny"]:
    """Choose chrome that leaves a row below the menu for terminal redraws."""
    rows = shutil.get_terminal_size(fallback=(80, 24)).lines
    if rows <= 3:
        return "tiny"
    if rows <= _HELP_CHROME_ROWS + 1:
        return "compact"
    return "regular"


def _help_viewport_height() -> int:
    """Rows the help list may use without scrolling its title or controls away."""
    rows = shutil.get_terminal_size(fallback=(80, 24)).lines
    layout = _help_layout()
    if layout == "tiny":
        return 1
    chrome_rows = {
        "regular": _HELP_CHROME_ROWS,
        "compact": _HELP_COMPACT_CHROME_ROWS,
    }[layout]
    # Leave one row below the menu so a redraw or the prompt does not force the
    # title off-screen when the cursor is already on the terminal's last line.
    return min(_HELP_VIEW_ROWS, max(1, rows - chrome_rows - 1))


def _help_menu_height(
    viewport_height: int, *, layout: Literal["regular", "compact", "tiny"]
) -> int:
    if layout == "regular":
        # leading blank, title, counter, rule, rows, blank, hint
        return _HELP_CHROME_ROWS + viewport_height
    if layout == "compact":
        # combined title/counter, rows, hint
        return _HELP_COMPACT_CHROME_ROWS + viewport_height
    return viewport_height


def _help_hint(*, width: int, detail_pages: int) -> str:
    """Controls that fit on one physical terminal row."""
    full = _HELP_PAGED_HINT if detail_pages > 1 else _HELP_HINT
    if len(full) <= width:
        return full
    narrow = _HELP_NARROW_PAGED_HINT if detail_pages > 1 else _HELP_NARROW_HINT
    return _clip(narrow, width)


def _detail_page(
    rows: Sequence[HelpDisplayRow],
    *,
    selected: int,
    viewport_height: int,
    page: int,
) -> tuple[list[HelpDisplayRow] | None, int]:
    """Return the selected command and one page of oversized expanded details."""
    detail_end = _detail_end_index(rows, selected)
    detail_count = detail_end - selected - 1
    if not detail_count:
        return None, 0
    if viewport_height == 1:
        pages = detail_count + 1
        current_page = min(max(0, page), pages - 1)
        return [rows[selected + current_page]], pages
    page_size = max(1, viewport_height - 1)
    pages = (detail_count + page_size - 1) // page_size
    if pages <= 1:
        return None, pages
    current_page = min(max(0, page), pages - 1)
    detail_start = selected + 1 + current_page * page_size
    return [rows[selected], *rows[detail_start : detail_start + page_size]], pages


def _detail_page_count(
    rows: Sequence[HelpDisplayRow],
    *,
    selected: int,
    viewport_height: int,
) -> int:
    """Return the number of pages required for the selected command's details."""
    detail_count = _detail_end_index(rows, selected) - selected - 1
    if not detail_count:
        return 0
    if viewport_height == 1:
        return detail_count + 1
    return (detail_count + viewport_height - 2) // (viewport_height - 1)


def _draw_help_menu(
    rows: Sequence[HelpRow],
    *,
    selected: int,
    expanded: int | None,
    erase_lines: int,
    viewport_height: int | None = None,
    detail_page: int = 0,
) -> int:
    width = menu_columns()
    layout = _help_layout()
    display = _display_rows(rows, expanded)
    display_selected = _display_index_for_source(display, selected)
    available_viewport_height = _help_viewport_height()
    base_viewport_height = available_viewport_height if viewport_height is None else viewport_height
    effective_viewport_height = _expanded_viewport_height(
        display,
        display_selected,
        base_viewport_height,
    )
    if viewport_height is None:
        effective_viewport_height = min(effective_viewport_height, available_viewport_height)
    start, end = _viewport_bounds(display, display_selected, effective_viewport_height)
    visible = display[start:end]
    paged_visible, detail_pages = _detail_page(
        display,
        selected=display_selected,
        viewport_height=effective_viewport_height,
        page=detail_page,
    )
    if paged_visible is not None:
        visible = paged_visible
    height = _help_menu_height(effective_viewport_height, layout=layout)
    if erase_lines:
        erase_menu_lines(erase_lines)

    selected_count = sum(1 for row in rows[: selected + 1] if row.selectable)
    total_count = sum(1 for row in rows if row.selectable)

    if layout == "regular":
        write_menu_line()
        write_menu_line(
            f"{ui_theme.PROMPT_ACCENT_ANSI}{_center('Slash commands', width)}{ui_theme.ANSI_RESET}"
        )
        write_menu_line(
            f"{ui_theme.DIM_COUNTER_ANSI}{selected_count}/{total_count}{ui_theme.ANSI_RESET}"
        )
        write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{_separator_rule(width)}{ui_theme.ANSI_RESET}")
    elif layout == "compact":
        title = _clip(f"Slash commands  {selected_count}/{total_count}", width)
        write_menu_line(f"{ui_theme.PROMPT_ACCENT_ANSI}{title}{ui_theme.ANSI_RESET}")
    else:
        command = rows[selected].command
        title = _clip(
            f"Slash commands {selected_count}/{total_count}: {command.name if command else ''}",
            width,
        )
        write_menu_line(f"{ui_theme.PROMPT_ACCENT_ANSI}{title}{ui_theme.ANSI_RESET}")
        visible = []
    if layout != "tiny":
        for row in visible:
            write_menu_line(
                _render_display_row(
                    row,
                    selected=(row.source_index == selected),
                    expanded=(row.source_index == expanded),
                    width=width,
                )
            )
        for _ in range(max(0, effective_viewport_height - len(visible))):
            write_menu_line()
    if layout != "tiny":
        if layout == "regular":
            write_menu_line()
        hint = _help_hint(width=width, detail_pages=detail_pages)
        write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{hint}{ui_theme.ANSI_RESET}")
    sys.stdout.flush()
    return height


def choose_help_command(sections: Sequence[HelpSection]) -> str | None:
    """Let a TTY user browse command details from a grouped viewport."""
    rows = _flatten_help_rows(sections)
    selected = _first_selectable_index(rows)
    if selected is None:
        return None

    erase_lines = 0
    expanded: int | None = None
    detail_page = 0
    while True:
        erase_lines = _draw_help_menu(
            rows,
            selected=selected,
            expanded=expanded,
            erase_lines=erase_lines,
            detail_page=detail_page,
        )
        action = read_menu_action(horizontal=expanded is not None)
        if action == "space":
            command = rows[selected].command
            if command is not None and has_help_details(command):
                expanded = None if expanded == selected else selected
                detail_page = 0
            continue
        if action == "enter":
            command = rows[selected].command
            erase_menu_lines(erase_lines, delete=True)
            return command.name if command is not None else None
        if action in ("cancel", "eof"):
            erase_menu_lines(erase_lines, delete=True)
            return None
        if action == "ignore":
            continue
        if action == "left" and expanded is not None:
            detail_page = max(0, detail_page - 1)
        elif action == "right" and expanded is not None:
            display = _display_rows(rows, expanded)
            display_selected = _display_index_for_source(display, selected)
            page_count = _detail_page_count(
                display,
                selected=display_selected,
                viewport_height=_help_viewport_height(),
            )
            detail_page = min(detail_page + 1, max(0, page_count - 1))
        elif action == "up":
            selected = _next_selectable_index(rows, selected, -1)
            expanded = None
            detail_page = 0
        elif action == "down":
            selected = _next_selectable_index(rows, selected, 1)
            expanded = None
            detail_page = 0


__all__ = [
    "HelpSection",
    "HelpRow",
    "choose_help_command",
    "render_command_detail",
    "render_help_index",
    "render_section_detail",
]
