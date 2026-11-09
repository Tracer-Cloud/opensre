"""Ctrl+O expands stashed collapsed tool-output peeks (Droid-style).

A long tool result renders a short head plus ``… N more, Ctrl+O to view``.
This hook expands the full text — inline into scrollback when modest, or in
``$PAGER`` / ``less`` when large — then returns to the session. The binding is
gated on a collapsed body being stashed, so Ctrl+O falls through when nothing
was folded. Repeated presses cycle the last N peeks (newest first).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from rich.text import Text

from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.interactive_shell.session.terminal_session import (
    INLINE_EXPAND_MAX_CHARS,
    INLINE_EXPAND_MAX_LINES,
)


def _fits_inline(text: str) -> bool:
    if len(text) > INLINE_EXPAND_MAX_CHARS:
        return False
    return text.count("\n") <= INLINE_EXPAND_MAX_LINES


def _safe_expand_text(text: str) -> str:
    """Strip ANSI/CSI and leftover controls so expand cannot trash the live TUI."""
    plain = Text.from_ansi(text).plain
    return strip_terminal_controls(plain, keep_whitespace=True)


def _print_inline_expand(text: str) -> None:
    """Paint the peek into scrollback so everyday peeks never leave the TUI."""
    body = _safe_expand_text(text)
    if not body.endswith("\n"):
        body = f"{body}\n"
    # Framed like other tool output: dim gutter, no pager chrome.
    sys.stdout.write(f"\n  ↳ expanded\n{body}")
    sys.stdout.flush()


def expand_collapsed_output(text: str) -> None:
    """Show *text* inline when modest; otherwise ``$PAGER`` / ``less`` / print."""
    safe = _safe_expand_text(text)
    if _fits_inline(safe):
        _print_inline_expand(safe)
        return
    spec = os.environ.get("PAGER") or shutil.which("less") or ""
    cmd = shlex.split(spec) if spec else []
    if cmd:
        subprocess.run(cmd, input=safe.encode(), check=False)
        return
    sys.stdout.write(safe if safe.endswith("\n") else f"{safe}\n")
    sys.stdout.flush()


def install_output_expand_key_bindings(
    has_output: Callable[[], bool],
    get_output: Callable[[], str],
    expand: Callable[[str], None],
) -> KeyBindings:
    """Bind Ctrl+O to expand the next stashed collapsed tool result."""
    kb = KeyBindings()
    output_shown = Condition(has_output)

    @kb.add("c-o", filter=output_shown, eager=True)
    def _expand_output(_event: KeyPressEvent) -> None:
        body = get_output()
        if body:
            expand(body)

    return kb


__all__ = ["expand_collapsed_output", "install_output_expand_key_bindings"]
