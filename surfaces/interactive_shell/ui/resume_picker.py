"""Scrollable terminal picker for resumable conversations."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.scrollable_picker import choose_scrollable
from surfaces.shared.terminal.components.choice_menu import (
    erase_menu_lines,
    menu_columns,
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
    is_current: bool = False
    detail: str = ""


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
    title = clip_prompt_text(
        f"● current  {item.title}" if item.is_current else item.title, title_width
    )
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
        f"{ui_theme.HIGHLIGHT_ANSI if item.is_current else title_styles[index % len(title_styles)]}"
        f"{title}{padding}{ui_theme.ANSI_RESET}"
    )


def _draw(
    items: Sequence[ResumeMenuItem],
    *,
    selected: int,
    top: int,
    visible_rows: int,
    erase_lines: int,
    now: datetime,
    heading: str = "Resume session",
) -> int:
    width = menu_columns()
    if erase_lines:
        erase_menu_lines(erase_lines)
    write_menu_line()
    write_menu_line(
        f"{ui_theme.PROMPT_ACCENT_ANSI}"
        f"{clip_prompt_text(f'  {heading}', width)}{ui_theme.ANSI_RESET}"
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
    if items[selected].detail and width < 64:
        more = ("↑" if top else "") + ("↓" if end < len(items) else "")
    position = f"{selected + 1}/{len(items)}"
    status_detail = "  ".join(part for part in (more, items[selected].detail) if part)
    status = clip_prompt_text(f"  {status_detail}", max(0, width - len(position) - 2))
    status += " " * max(0, width - prompt_text_width(status) - len(position) - 2)
    status += f"{position}  "
    write_menu_line(
        f"{ui_theme.DIM_COUNTER_ANSI}{clip_prompt_text(status, width)}{ui_theme.ANSI_RESET}"
    )
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{'─' * width}{ui_theme.ANSI_RESET}")
    footer = (
        "  Enter already here   ↑↓/j/k move   Esc exit" if items[selected].is_current else _FOOTER
    )
    write_menu_line(
        f"{ui_theme.DIM_COUNTER_ANSI}{clip_prompt_text(footer, width)}{ui_theme.ANSI_RESET}"
    )
    sys.stdout.flush()
    return visible_rows + _CHROME_ROWS


def choose_resume_session(
    items: Sequence[ResumeMenuItem],
    *,
    heading: str = "Resume session",
) -> str | None:
    """Select a conversation by scrolling a bounded terminal viewport."""
    oldest = datetime.min.replace(tzinfo=UTC)
    ordered = sorted(items, key=lambda item: _as_datetime(item.activity_at) or oldest, reverse=True)
    now = datetime.now(UTC)
    selected = next((index for index, item in enumerate(ordered) if not item.is_current), 0)
    picked = choose_scrollable(
        ordered,
        draw=lambda rows, **kwargs: _draw(rows, now=now, heading=heading, **kwargs),
        max_visible_rows=_MAX_VISIBLE_ROWS,
        chrome_rows=_CHROME_ROWS,
        selected=selected,
    )
    return picked.session_id if picked is not None else None
