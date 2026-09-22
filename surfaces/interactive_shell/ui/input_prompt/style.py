"""prompt-toolkit style construction and live theme refresh."""

from __future__ import annotations

from contextlib import suppress

from prompt_toolkit.styles import Style

from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.runtime import Session


def _build_prompt_style() -> Style:
    theme = ui_theme.get_active_theme()
    text_fg = f"fg:{theme.TEXT}"
    # Distinct composer plate — same role as Claude/Cursor/Droid's input surface.
    surface = f"bg:{theme.INPUT_SURFACE}"
    selection = f"bg:{ui_theme.menu_selection_hex()}"
    return Style.from_dict(
        {
            "prompt-frame-line": f"bold {theme.HIGHLIGHT}",
            "": text_fg,
            "default": text_fg,
            "placeholder": f"{theme.DIM} {surface}",
            "repl-slash-command": f"bold {theme.HIGHLIGHT} {surface}",
            "frame": surface,
            "frame.border": f"{theme.SECONDARY} {surface}",
            "composer": f"{theme.TEXT} {surface}",
            "composer-body": f"{theme.TEXT} {surface}",
            "command-tray": f"{theme.TEXT} {surface}",
            "command-tray.description": f"{theme.SECONDARY} {surface}",
            "command-tray.hint": f"{theme.SECONDARY} {surface}",
            "command-tray.current": f"bold {theme.HIGHLIGHT} {selection}",
            "command-tray.current.description": f"nobold {theme.TEXT} {selection}",
            # Footer sits under the plate on terminal bg (hint chrome, not input).
            "composer-footer": theme.DIM,
            # prompt_toolkit defaults the ``bottom-toolbar`` style to
            # ``reverse:noinherit``, which paints the toolbar as a dark
            # highlighted band across the terminal. Clear the reverse
            # so the spinner + hint sit on the regular terminal bg
            # (Claude Code-style flat layout).
            "bottom-toolbar": "noreverse",
            "bottom-toolbar.text": "noreverse",
        }
    )


def refresh_prompt_theme(session: Session) -> None:
    """Apply the active palette to the running prompt (input text + placeholder)."""
    app = session.terminal.prompt_app
    if app is None:
        return
    app.style = _build_prompt_style()
    # Between prompt_async turns the Application is not running; invalidate() then
    # triggers ESC[6n CPR queries whose responses leak as literal text on the
    # next idle-hint line (e.g. ``^[[1;1R/ for commands``).
    if not app.is_running:
        return
    if app.renderer is not None:
        with suppress(Exception):
            app.renderer.clear()
    app.invalidate()
