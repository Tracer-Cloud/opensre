"""REPL TTY plumbing: buffered print helpers and table factory.

Keeps cursor at column zero and normalises line endings under prompt_toolkit's
patch_stdout so Rich tables and JSON don't render as diagonal blocks.

Domain-specific table renderers live in :mod:`tables`.
"""

from __future__ import annotations

import io
import shutil
import sys
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Literal, cast

from rich import box
from rich.console import Console
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

_REPL_OUTPUT_PREPARED = ContextVar("_REPL_OUTPUT_PREPARED", default=False)


def _repl_output_already_prepared() -> bool:
    """Whether current call stack already prepared the TTY for Rich output."""
    return _REPL_OUTPUT_PREPARED.get()


def _console_print_prepared(console: Console, *objects: Any, **kwargs: Any) -> None:
    token = _REPL_OUTPUT_PREPARED.set(True)
    try:
        console.print(*objects, **kwargs)
    finally:
        _REPL_OUTPUT_PREPARED.reset(token)


def _repl_table_width(console: Console) -> int:
    """Best-effort terminal width for Rich tables after inline menu I/O."""
    term_cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    # Keep one safety column to avoid right-edge auto-wrap artifacts in some
    # terminals (first-char clipping / duplicate right border when a row lands
    # exactly on the terminal width).
    return max(40, min(console.width, term_cols) - 1)


def repl_output_width(console: Console) -> int:
    """The width :func:`print_repl_renderable` renders at; size full-width rows to it."""
    return _repl_table_width(console)


def _prepare_tty_for_rich(console: Console) -> int:
    """Return the width Rich should render at.

    prepare_repl_output_line() (which writes \\r\\n) is intentionally NOT called
    here. Under patch_stdout(raw=True), that extra newline causes the bottom
    toolbar text to flush into the output stream before the table renders. Slash
    commands start after the user presses Enter, so the cursor is already on a
    fresh line; no extra line-feed is needed.
    """
    return _repl_table_width(console)


def _normalize_repl_line_endings(text: str) -> str:
    """Convert Rich output to ``\\r\\n`` so each line starts at column zero."""
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _console_is_capturing(console: Console) -> bool:
    """True inside an open ``capture()`` block, where output must not reach the file."""
    return console._buffer_index > 0


def _feed_record_buffer(console: Console, plain_text: str) -> None:
    """Append text the direct-stdout path wrote so ``export_text`` still sees it.

    Every slash command runs under ``capture_console_segment``, which turns on
    ``record`` so the analytics transcript gets a plain-text copy of what was
    printed. That must not push tables onto the row-by-row ``console.print``
    path — under ``patch_stdout(raw=True)`` each row is a bare ``\\n`` and the
    table staircases across the screen. Recording is fed here instead.
    """
    if not console.record:
        return
    # A delegating console (StreamingConsole) exports from the console it
    # renders through; the record buffer that matters lives on that one.
    owner = getattr(console, "_output", None) or console
    with owner._record_buffer_lock:
        owner._record_buffer.append(Segment(plain_text))


def _write_repl_tty_buffered(
    *,
    console: Console,
    width: int,
    leading_blank: bool,
    render_to_buffer: Callable[[Console], None],
) -> None:
    """Render Rich output to a buffer and write it in one TTY-safe stdout call."""
    buf = io.StringIO()
    # Inherit the caller's color depth so theme colours are not down-converted
    # on a truecolor terminal (Rich would otherwise re-detect from the env).
    buf_console = Console(
        file=buf,
        force_terminal=True,
        highlight=False,
        width=width,
        color_system=cast(
            Literal["auto", "standard", "256", "truecolor", "windows"],
            console.color_system or "auto",
        ),
    )
    render_to_buffer(buf_console)
    styled = buf.getvalue()
    rendered = _normalize_repl_line_endings(styled)
    if leading_blank:
        rendered = "\r\n" + rendered
    # Start at column zero regardless of where the previous writer (a status
    # spinner's teardown, a wrapped log line) left the cursor; otherwise the
    # first row begins mid-line and the rows below inherit the offset.
    # Start at column zero regardless of where the previous writer (a status
    # spinner's teardown, a wrapped log line) left the cursor; otherwise the
    # first row begins mid-line and the rows below inherit the offset.
    rendered = "\r" + rendered
    token = _REPL_OUTPUT_PREPARED.set(True)
    try:
        sys.stdout.write(rendered)
        sys.stdout.flush()
    finally:
        _REPL_OUTPUT_PREPARED.reset(token)
    _feed_record_buffer(console, Text.from_ansi(styled).plain)


def print_repl_table(console: Console, table: Table, *, width: int | None = None) -> None:
    """Print a Rich table using REPL-safe TTY width.

    When the console writes to sys.stdout (the real REPL path), tables are
    rendered into a string buffer first and written in a single sys.stdout.write
    call with explicit \\r\\n line endings. This prevents the diagonal-render
    artifact that occurs under prompt_toolkit's patch_stdout: each table row is
    a separate Rich write, and if the terminal or proxy does not convert \\n to
    \\r\\n, every row starts where the previous one ended instead of column zero.

    When the console writes to a non-TTY stdout (piped output) or to a
    different file (e.g. a StringIO in tests), the normal console.print path
    is used — preserving the caller's color_system and avoiding ANSI pollution
    in piped output.
    """
    leading_blank = width is None
    width = width if width is not None else _prepare_tty_for_rich(console)
    if console.file is sys.stdout and sys.stdout.isatty() and not _console_is_capturing(console):
        _write_repl_tty_buffered(
            console=console,
            width=width,
            leading_blank=leading_blank,
            render_to_buffer=lambda buf_console: buf_console.print(table),
        )
    else:
        if leading_blank:
            _console_print_prepared(console)
        _console_print_prepared(console, table, width=width)


def print_repl_json(console: Console, json_str: str) -> None:
    """Print JSON via Rich using REPL-safe \\r\\n line endings.

    Mirrors the buffered-write approach in :func:`print_repl_table` to prevent
    the diagonal-render artifact under prompt_toolkit's patch_stdout: bare
    ``\\n`` from Rich does not imply a carriage-return, so each JSON line would
    start at the column where the previous one ended.  Rendering to a buffer
    and normalising to ``\\r\\n`` ensures every line begins at column zero.
    The leading blank is included in the same write call to avoid a stale CPR
    sequence being left in stdin by a prompt_toolkit toolbar flush.
    """
    width = _prepare_tty_for_rich(console)
    if console.file is sys.stdout and sys.stdout.isatty() and not _console_is_capturing(console):
        _write_repl_tty_buffered(
            console=console,
            width=width,
            leading_blank=True,
            render_to_buffer=lambda buf_console: buf_console.print_json(json_str),
        )
    else:
        token = _REPL_OUTPUT_PREPARED.set(True)
        try:
            console.print_json(json_str)
        finally:
            _REPL_OUTPUT_PREPARED.reset(token)


def print_repl_text(console: Console, text: str, *, markup: bool = False) -> None:
    """Print multi-line plain text with CRLF under ``patch_stdout(raw=True)``.

    Session-goal progress and similar checklists use bare newlines. Rich's
    row-by-row ``console.print`` emits ``\\n`` only; in raw mode that does not
    return the cursor to column zero, so the next lines staircase across the
    screen. Buffer + ``\\r\\n`` (same path as tables/JSON) keeps each row left-
    aligned.
    """
    if not text:
        return
    if console.file is sys.stdout and sys.stdout.isatty() and not _console_is_capturing(console):
        width = _prepare_tty_for_rich(console)
        _write_repl_tty_buffered(
            console=console,
            width=width,
            leading_blank=False,
            render_to_buffer=lambda buf_console: buf_console.print(text, markup=markup),
        )
        return
    _console_print_prepared(console, text, markup=markup)


def print_repl_renderable(console: Console, renderable: Any) -> None:
    """Print one Rich renderable with CRLF under ``patch_stdout(raw=True)``.

    Same buffered path as :func:`print_repl_text`, for styled multi-row
    output (``Text``/``Group``) that would otherwise staircase in raw mode.
    """
    if console.file is sys.stdout and sys.stdout.isatty() and not _console_is_capturing(console):
        width = _prepare_tty_for_rich(console)
        _write_repl_tty_buffered(
            console=console,
            width=width,
            leading_blank=False,
            render_to_buffer=lambda buf_console: buf_console.print(renderable),
        )
        return
    _console_print_prepared(console, renderable)


def hyperlink(url: str, *, style: str = "") -> Text:
    """The URL as clickable terminal text (OSC 8), still readable where links are unsupported.

    The visible text stays the URL itself, so terminals that only auto-detect
    URLs (or none at all) still show something the user can copy.
    """
    link_style = f"{style} link {url}".strip()
    return Text(url, style=link_style)


def repl_print(console: Console, *objects: Any, **kwargs: Any) -> None:
    """Print via Rich after resetting the TTY column (inline-menu safe)."""
    from surfaces.shared.terminal.components.choice_menu import prepare_repl_output_line

    prepare_repl_output_line()
    _console_print_prepared(console, *objects, **kwargs)


def repl_print_continue(console: Console, *objects: Any, **kwargs: Any) -> None:
    """Print via Rich at column zero without inserting a blank line.

    Use for output that continues the current block (e.g. a command's stdout
    directly under its ``$ command`` header). :func:`repl_print` would prepend
    a ``\\r\\n``, visually detaching the output from its header.
    """
    from surfaces.shared.terminal.components.choice_menu import ensure_tty_column_zero

    ensure_tty_column_zero()
    _console_print_prepared(console, *objects, **kwargs)


def _repl_write_buffer(rendered: str) -> None:
    """Flush pre-rendered Rich output with CRLF line endings (patch_stdout safe)."""
    from surfaces.shared.terminal.components.cpr_stdin import strip_cpr_escape_sequences

    normalized = strip_cpr_escape_sequences(rendered.replace("\r\n", "\n").replace("\n", "\r\n"))
    token = _REPL_OUTPUT_PREPARED.set(True)
    try:
        sys.stdout.write(normalized)
        sys.stdout.flush()
    finally:
        _REPL_OUTPUT_PREPARED.reset(token)


def repl_clear_screen() -> None:
    """Clear the terminal scrollback when the REPL runs under patch_stdout."""
    if not sys.stdout.isatty():
        return
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def repl_table(**kwargs: Any) -> Table:
    """Minimal outer borders — closer to Claude Code than full ASCII grids."""
    opts: dict[str, Any] = {
        "box": box.MINIMAL_HEAVY_HEAD,
        "show_edge": False,
        "pad_edge": False,
        "title_justify": "left",
    }
    opts.update(kwargs)
    return Table(**opts)


__all__ = [
    "_repl_output_already_prepared",
    "_repl_table_width",
    "hyperlink",
    "print_repl_json",
    "print_repl_renderable",
    "print_repl_table",
    "print_repl_text",
    "repl_clear_screen",
    "repl_output_width",
    "repl_print",
    "repl_print_continue",
    "repl_table",
]
