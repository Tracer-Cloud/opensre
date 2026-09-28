"""Scrollable terminal picker for resumable conversations."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

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

_MAX_VISIBLE_ROWS = 18
_AGE_WIDTH = 8
_CHROME_ROWS = 6
_FOOTER = "  Enter resume   ↑↓/j/k move   Esc exit"


@dataclass(frozen=True)
class ResumeMenuItem:
    session_id: str
    title: str
    activity_at: str | datetime | int | float | None


def _visible_row_count() -> int:
    terminal_rows = shutil.get_terminal_size(fallback=(80, 24)).lines
    # Leave one row below the menu for the cursor. The erase helper can only
    # climb ``terminal_rows - 1`` rows without touching prior scrollback.
    available = terminal_rows - _CHROME_ROWS - 1
    return min(_MAX_VISIBLE_ROWS, max(1, available))


def _as_datetime(value: str | datetime | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        then = value
    elif isinstance(value, (int, float)):
        try:
            then = datetime.fromtimestamp(float(value), tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    else:
        try:
            then = datetime.fromisoformat(value)
        except ValueError:
            return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return then.astimezone(UTC)


def _time_ago(value: str | datetime | int | float | None, now: datetime) -> str:
    then = _as_datetime(value)
    if then is None:
        return "unknown"
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return "now" if seconds < 10 else f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 31536000:
        return f"{seconds // 86400}d ago"
    return f"{seconds // 31536000}y ago"


def _row(item: ResumeMenuItem, *, selected: bool, index: int, width: int, now: datetime) -> str:
    age = clip_prompt_text(_time_ago(item.activity_at, now), _AGE_WIDTH)
    prefix = clip_prompt_text(f"  {'›' if selected else ' '} {age:<{_AGE_WIDTH}}  ", width)
    title_width = max(0, width - prompt_text_width(prefix))
    title = clip_prompt_text(item.title, title_width)
    padding = " " * max(0, width - prompt_text_width(prefix + title))
    if selected:
        return (
            f"{ui_theme.prominent_menu_selection_ansi()}"
            f"{prefix}{title}{padding}{ui_theme.ANSI_RESET}"
        )
    title_styles = (
        ui_theme.HIGHLIGHT_ANSI,
        ui_theme.BRAND_ANSI,
        ui_theme.TEXT_ANSI,
        ui_theme.SECONDARY_ANSI,
    )
    background = ui_theme.INPUT_SURFACE_BG_ANSI if index % 2 else ui_theme.SURFACE_BG_ANSI
    return (
        f"{background}{ui_theme.DIM_COUNTER_ANSI}{prefix}"
        f"{title_styles[index % len(title_styles)]}{title}{padding}{ui_theme.ANSI_RESET}"
    )


def _draw(
    items: Sequence[ResumeMenuItem],
    *,
    selected: int,
    top: int,
    visible_rows: int,
    erase_lines: int,
    now: datetime,
) -> int:
    width = menu_columns()
    if erase_lines:
        erase_menu_lines(erase_lines)
    write_menu_line()
    write_menu_line(
        f"{ui_theme.PROMPT_ACCENT_ANSI}"
        f"{clip_prompt_text('  Resume session', width)}{ui_theme.ANSI_RESET}"
    )
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{'─' * width}{ui_theme.ANSI_RESET}")
    end = min(len(items), top + visible_rows)
    for index in range(top, end):
        write_menu_line(
            _row(items[index], selected=index == selected, index=index, width=width, now=now)
        )
    for _ in range(visible_rows - (end - top)):
        write_menu_line()
    more = "↑ earlier" if top else ""
    if end < len(items):
        more = f"{more}    ↓ more" if more else "↓ more"
    position = f"{selected + 1}/{len(items)}"
    status = f"  {more}" if more else ""
    status += " " * max(0, width - prompt_text_width(status) - len(position) - 2)
    status += f"{position}  "
    write_menu_line(
        f"{ui_theme.DIM_COUNTER_ANSI}{clip_prompt_text(status, width)}{ui_theme.ANSI_RESET}"
    )
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{'─' * width}{ui_theme.ANSI_RESET}")
    write_menu_line(
        f"{ui_theme.DIM_COUNTER_ANSI}{clip_prompt_text(_FOOTER, width)}{ui_theme.ANSI_RESET}"
    )
    sys.stdout.flush()
    return visible_rows + _CHROME_ROWS


def choose_resume_session(items: Sequence[ResumeMenuItem]) -> str | None:
    """Select a conversation by scrolling a bounded terminal viewport."""
    if not items or not repl_tty_interactive():
        return None
    oldest = datetime.min.replace(tzinfo=UTC)
    ordered = sorted(items, key=lambda item: _as_datetime(item.activity_at) or oldest, reverse=True)
    selected = 0
    top = 0
    drawn_height = 0
    now = datetime.now(UTC)
    enter_inline_menu()
    try:
        while True:
            visible_rows = _visible_row_count()
            top = min(top, max(0, len(ordered) - visible_rows))
            if selected < top:
                top = selected
            elif selected >= top + visible_rows:
                top = selected - visible_rows + 1
            drawn_height = _draw(
                ordered,
                selected=selected,
                top=top,
                visible_rows=visible_rows,
                erase_lines=drawn_height,
                now=now,
            )
            action = read_menu_action()
            if action == "up":
                selected = (selected - 1) % len(ordered)
            elif action == "down":
                selected = (selected + 1) % len(ordered)
            elif action == "enter":
                return ordered[selected].session_id
            elif action in ("cancel", "eof"):
                return None
    finally:
        erase_menu_lines(drawn_height, delete=True)
        leave_inline_menu()
