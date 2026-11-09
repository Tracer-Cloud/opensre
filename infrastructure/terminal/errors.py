"""Terminal rendering for user-facing CLI errors."""

from __future__ import annotations

import traceback
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


def render_error(
    exc: BaseException,
    *,
    console: Console | None = None,
    hint: str | None = None,
) -> None:
    """Display a clean, user-facing error without a raw traceback."""
    from infrastructure.terminal.theme import (
        DIM,
        ERROR,
        GLYPH_ERROR,
        SECONDARY,
        TEXT,
    )

    _console = console or Console(stderr=True, highlight=False)

    exc_type = type(exc).__name__
    exc_msg = str(exc).strip() or "(no detail)"

    frame_line = ""
    tb = exc.__traceback__
    if tb is not None:
        frames = traceback.extract_tb(tb)
        if frames:
            frame = frames[-1]
            try:
                path = str(Path(frame.filename).relative_to(Path.cwd()))
            except ValueError:
                path = frame.filename
            frame_line = f"{path}:{frame.lineno} in {frame.name}"

    _hint = hint or "Run opensre doctor to diagnose environment issues."

    body = Text()
    body.append(f"  {GLYPH_ERROR}  ", style=f"bold {ERROR}")
    body.append(exc_type, style=f"bold {ERROR}")
    body.append("\n")

    body.append(f"     {exc_msg}", style=TEXT)
    body.append("\n")

    if frame_line:
        body.append(f"     {frame_line}", style=DIM)
        body.append("\n")

    body.append(f"     {_hint}", style=SECONDARY)

    _console.print()
    _console.print(Panel(body, border_style=DIM, padding=(0, 1), expand=False))
    _console.print()
