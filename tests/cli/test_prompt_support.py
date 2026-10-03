from __future__ import annotations

import time

import pytest
import questionary
from prompt_toolkit.input.defaults import create_pipe_input  # type: ignore[import-not-found]
from prompt_toolkit.output import DummyOutput  # type: ignore[import-not-found]

from infrastructure.terminal.prompt_support import (
    _last_ctrl_c,
    handle_ctrl_c_press,
    install_questionary_ctrl_c_double_exit,
    install_questionary_escape_cancel,
    print_session_resume_hint,
)


def test_print_session_resume_hint_includes_repl_and_cli_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from io import StringIO

    from rich.console import Console

    import infrastructure.terminal.theme as ui_theme

    ui_theme.set_active_theme("amber")
    monkeypatch.setattr("infrastructure.terminal.prompt_support.sys.argv", ["o"])
    console = Console(
        file=StringIO(),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        no_color=False,
    )
    print_session_resume_hint(console, "8988e743-87ae-4c4c-a37b-0351e62a4855")
    output = console.file.getvalue()
    assert "Resume this session with:" in output
    assert "/resume 8988e743-87ae-4c4c-a37b-0351e62a4855" in output
    assert "o --resume 8988e743-87ae-4c4c-a37b-0351e62a4855" in output
    # Accent theme on the copy-pasteable commands — not flat DIM (looks unthemed).
    assert ui_theme.HIGHLIGHT_ANSI in output


def test_exit_farewell_keeps_theme_accent(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/exit`` goodbye and resume cmds must use HIGHLIGHT, not plain DIM."""
    from io import StringIO

    from rich.console import Console

    import infrastructure.terminal.theme as ui_theme
    from surfaces.interactive_shell.command_registry.system import _cmd_exit
    from surfaces.interactive_shell.runtime import Session

    ui_theme.set_active_theme("amber")
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.exit_control._flush_analytics_on_exit",
        lambda _console: None,
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.choice_menu.prepare_repl_output_line",
        lambda: None,
    )
    session = Session()
    session.session_id = "0dc1aa80-efdf-4245-a42a-36ea06d14964"
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        no_color=False,
    )
    assert _cmd_exit(session, console, []) is False
    out = buf.getvalue()
    assert "goodbye." in out
    assert "/resume 0dc1aa80-efdf-4245-a42a-36ea06d14964" in out
    assert ui_theme.HIGHLIGHT_ANSI in out


def test_install_questionary_escape_cancel_is_idempotent() -> None:
    install_questionary_escape_cancel()
    first = questionary.select
    install_questionary_escape_cancel()
    assert questionary.select is first


def test_stock_questionary_select_escape_cancels() -> None:
    install_questionary_escape_cancel()
    with create_pipe_input() as pipe_input:
        q = questionary.select(
            "Pick",
            choices=["a", "b"],
            input=pipe_input,
            output=DummyOutput(),
        )
        pipe_input.send_bytes(b"\x1b")
        app = q.application
        app.input = pipe_input
        app.output = DummyOutput()
        assert app.run() is None


def test_stock_questionary_confirm_escape_cancels() -> None:
    """Verify that pressing Escape cancels a confirm prompt (Issue #1117).

    Sends the Escape byte (\\x1b) to a questionary.confirm application
    and asserts that it returns None instead of hanging.
    """
    install_questionary_escape_cancel()
    with create_pipe_input() as pipe_input:
        q = questionary.confirm(
            "Are you sure?",
            input=pipe_input,
            output=DummyOutput(),
        )
        pipe_input.send_bytes(b"\x1b")
        app = q.application
        app.input = pipe_input
        app.output = DummyOutput()
        assert app.run() is None


def test_stock_questionary_text_escape_cancels() -> None:
    """Verify that pressing Escape cancels a text input prompt (Issue #1117).

    Sends the Escape byte (\\x1b) to a questionary.text application
    and asserts that it returns None.
    """
    install_questionary_escape_cancel()
    with create_pipe_input() as pipe_input:
        q = questionary.text(
            "Name",
            input=pipe_input,
            output=DummyOutput(),
        )
        pipe_input.send_bytes(b"\x1b")
        app = q.application
        app.input = pipe_input
        app.output = DummyOutput()
        assert app.run() is None


def test_stock_questionary_path_escape_cancels() -> None:
    """Verify that pressing Escape cancels a path selection prompt (Issue #1117).

    Sends the Escape byte (\\x1b) to a questionary.path application
    and asserts that it returns None.
    """
    install_questionary_escape_cancel()
    with create_pipe_input() as pipe_input:
        q = questionary.path(
            "Path",
            input=pipe_input,
            output=DummyOutput(),
        )
        pipe_input.send_bytes(b"\x1b")
        app = q.application
        app.input = pipe_input
        app.output = DummyOutput()
        assert app.run() is None


def test_install_questionary_ctrl_c_double_exit_is_idempotent() -> None:
    install_questionary_ctrl_c_double_exit()
    first = questionary.select
    install_questionary_ctrl_c_double_exit()
    assert questionary.select is first


def test_ctrl_c_first_press_shows_hint_and_reprompts(capsys) -> None:
    """First Ctrl+C prints the hint and re-displays the prompt; Enter then submits."""
    _last_ctrl_c[0] = None
    install_questionary_ctrl_c_double_exit()
    with create_pipe_input() as pipe_input:
        q = questionary.select(
            "Pick",
            choices=["a", "b"],
            input=pipe_input,
            output=DummyOutput(),
        )
        # Ctrl+C cancels the first run; Enter submits the re-displayed prompt.
        pipe_input.send_bytes(b"\x03\r")
        result = q.ask()
    assert "(Press Ctrl+C again to exit)" in capsys.readouterr().out
    # After the hint the prompt was re-run and "a" was selected (first choice).
    assert result == "a"


def test_ctrl_c_second_press_exits(capsys) -> None:
    # Simulate a previous Ctrl+C just now so the second press fires immediately.
    _last_ctrl_c[0] = time.monotonic()
    with pytest.raises(SystemExit) as exc_info:
        handle_ctrl_c_press()
    assert exc_info.value.code == 0
    assert "Goodbye" in capsys.readouterr().out


def test_ctrl_c_hint_resets_after_window(capsys) -> None:
    # A press older than the exit window should show the hint again, not exit.
    _last_ctrl_c[0] = None  # effectively "long ago"
    handle_ctrl_c_press()
    out = capsys.readouterr().out
    assert "(Press Ctrl+C again to exit)" in out


def test_questionary_ask_inside_running_event_loop_does_not_raise() -> None:
    """q.ask() called from within a running asyncio event loop must not raise.

    Regression test for Sentry issue #1650: asyncio.run() cannot be called
    from a running event loop — triggered when questionary prompts are shown
    inside the async REPL dispatch path.
    """
    import asyncio

    _last_ctrl_c[0] = None
    install_questionary_ctrl_c_double_exit()

    async def _run() -> object:
        with create_pipe_input() as pipe_input:
            q = questionary.select(
                "Pick",
                choices=["a", "b"],
                input=pipe_input,
                output=DummyOutput(),
            )
            pipe_input.send_bytes(b"\r")
            return q.ask()

    result = asyncio.run(_run())
    assert result == "a"
