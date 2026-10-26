"""/theme slash command: unknown-theme error path."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from surfaces.interactive_shell.command_registry.theme import _cmd_theme
from surfaces.interactive_shell.session import Session


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, width=120), buf


def test_unknown_theme_prints_plain_name() -> None:
    session = Session()
    console, buf = _console()

    assert _cmd_theme(session, console, ["not-a-real-theme"])
    assert "unknown theme: not-a-real-theme" in buf.getvalue()


def test_unknown_theme_with_markup_like_argument_does_not_raise() -> None:
    """A theme name that looks like Rich markup must not crash the handler."""
    session = Session()
    console, buf = _console()

    assert _cmd_theme(session, console, ["[/]"])
    assert "unknown theme: [/]" in buf.getvalue()

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_theme(session, console, ["[bold"])
    assert "unknown theme: [bold" in buf.getvalue()
