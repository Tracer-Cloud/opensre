"""Launch poster for the REPL: welcome panel, redrawn on theme change."""

from __future__ import annotations

import io
import sys

from rich.console import Console
from rich.markup import escape
from rich.text import Text

import infrastructure.terminal.theme as ui_theme
from surfaces.shared.terminal.banner import animate_launch_wordmark
from surfaces.shared.terminal.components.rendering import (
    _console_is_capturing,
    _console_print_prepared,
    _feed_record_buffer,
    _prepare_tty_for_rich,
    _repl_write_buffer,
    repl_clear_screen,
)


def _theme_notice_line(theme_notice: str) -> str:
    """REPL-safe ``theme set: <name>`` using the active palette (not stale imports)."""
    return (
        f"{ui_theme.HIGHLIGHT_ANSI}theme set: {escape(theme_notice)}{ui_theme.ANSI_RESET}\r\n\r\n"
    )


def repl_render_launch_poster(
    console: Console,
    *,
    session: object = None,
    theme_notice: str | None = None,
) -> None:
    """Render the welcome panel using REPL-safe CRLF writes."""
    from surfaces.interactive_shell.ui.terminal_ui import render_terminal_ui

    if console.file is sys.stdout and sys.stdout.isatty() and not _console_is_capturing(console):
        width = _prepare_tty_for_rich(console)
        buf = io.StringIO()
        buf_console = Console(
            file=buf,
            force_terminal=True,
            highlight=False,
            color_system="truecolor",
            legacy_windows=False,
            width=width,
        )
        render_terminal_ui(buf_console, session=session)
        prefix = _theme_notice_line(theme_notice) if theme_notice else ""
        animate_launch_wordmark(console)
        _repl_write_buffer(prefix + buf.getvalue())
        _feed_record_buffer(console, Text.from_ansi(buf.getvalue()).plain)
        return

    if theme_notice:
        _console_print_prepared(
            console,
            f"[{ui_theme.HIGHLIGHT}]theme set:[/] {escape(theme_notice)}",
        )
    render_terminal_ui(console, session=session)


def refresh_welcome_poster(
    console: Console,
    *,
    session: object = None,
    theme_notice: str | None = None,
) -> None:
    """Clear scrollback and redraw the welcome panel with the active theme."""
    from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes

    repl_clear_screen()
    # ``repl_clear_screen`` can trigger a toolbar DSR/CPR exchange; drain before writing.
    drain_stale_cpr_bytes()
    repl_render_launch_poster(console, session=session, theme_notice=theme_notice)


__all__ = ["refresh_welcome_poster", "repl_render_launch_poster"]
