"""Terminal presentation of recent sessions."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Mapping, Sequence
from typing import Any

from rich import box
from rich.console import Console
from rich.text import Text

from infrastructure.terminal.theme import BOLD_BRAND, DIM, HIGHLIGHT, SECONDARY
from surfaces.interactive_shell.ui.session_picker import SessionMenuItem
from surfaces.shared.terminal.components.rendering import (
    print_repl_table,
    repl_output_width,
    repl_table,
)
from surfaces.shared.terminal.components.time_format import (
    format_repl_duration,
    format_repl_timestamp,
)

_COMPACT_WIDTH = 99


def session_menu_items(
    entries: Sequence[Mapping[str, Any]],
    *,
    current_session_id: str,
    current_started_at: float,
    resumed_from_name: str | None,
) -> list[SessionMenuItem]:
    """Build picker rows from the same session summaries used by the printed list."""
    items: list[SessionMenuItem] = []
    for entry in entries:
        session_id = str(entry["session_id"])
        is_current = session_id == current_session_id
        items.append(
            SessionMenuItem(
                session_id=session_id,
                title=_session_name(
                    entry, is_current=is_current, resumed_from_name=resumed_from_name
                ),
                started=format_repl_timestamp(entry.get("started_at"), style="compact"),
                started_full=format_repl_timestamp(entry.get("started_at"), style="table"),
                duration=_session_duration(
                    entry, is_current=is_current, current_started_at=current_started_at
                ),
                turns=(str(entry["total_turns"]) if entry.get("total_turns") is not None else "—"),
                is_current=is_current,
                activity_at=entry.get("activity_at")
                or entry.get("started_at")
                or (current_started_at if is_current else None),
            )
        )
    return items


def _session_name(
    entry: Mapping[str, Any], *, is_current: bool, resumed_from_name: str | None
) -> str:
    name = str(entry.get("name") or "")
    if name.startswith("/") and entry.get("conversation_title"):
        name = str(entry["conversation_title"])
    if is_current and not name and resumed_from_name:
        name = f"↩ {resumed_from_name}"
    return name.strip() or "Untitled session"


def _session_duration(
    entry: Mapping[str, Any], *, is_current: bool, current_started_at: float
) -> str:
    duration_secs = entry.get("duration_secs")
    if is_current:
        with contextlib.suppress(OverflowError, ValueError):
            duration_secs = max(0, int(time.time() - current_started_at))
    return format_repl_duration(duration_secs)


def render_recent_sessions(
    console: Console,
    items: Sequence[SessionMenuItem],
) -> None:
    """Render a scannable session list that fits the active terminal width."""
    if not items:
        console.print(f"[{DIM}]No sessions recorded yet.[/]")
        return

    compact = repl_output_width(console) < _COMPACT_WIDTH
    table = repl_table(
        title=f"Recent sessions ({len(items)})",
        title_style=BOLD_BRAND,
        box=box.SIMPLE_HEAD,
        caption=(
            "/resume <id> to continue"
            if compact
            else "Resume with /resume <session ID>  ·  Browse with /resume"
        ),
        caption_justify="left",
        caption_style=DIM,
    )
    table.add_column("Session", ratio=1, min_width=12, max_width=48, no_wrap=True)
    if not compact:
        table.add_column("ID", width=8, no_wrap=True, style=SECONDARY)
        table.add_column("Started", width=16, no_wrap=True, style=DIM)
        table.add_column("Duration", width=8, no_wrap=True, style=DIM)
        table.add_column("Turns", width=5, justify="right", no_wrap=True, style=DIM)

    for item in items:
        label = Text()
        if item.is_current:
            label.append("● current  ", style=str(HIGHLIGHT))
        label.append(item.title, style="bold" if item.is_current else "")

        if compact:
            label.append("\n")
            label.append(
                f"{item.session_id[:8]}  {item.started}  {item.duration}  {item.turns}t",
                style=str(DIM),
            )
            table.add_row(label)
        else:
            table.add_row(
                label,
                Text(item.session_id[:8]),
                Text(item.started_full),
                Text(item.duration),
                Text(item.turns),
            )

    print_repl_table(console, table)
