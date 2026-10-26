from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
import questionary
from prompt_toolkit.input.defaults import create_pipe_input  # type: ignore[import-not-found]
from prompt_toolkit.output import DummyOutput  # type: ignore[import-not-found]

from infrastructure.terminal.prompt_support import (
    _exit_in_progress,
    _exit_interrupted,
    _last_ctrl_c,
    ctrl_c_exit_interrupted,
    handle_ctrl_c_press,
    install_questionary_ctrl_c_double_exit,
    install_questionary_escape_cancel,
    print_session_resume_hint,
    repl_prompt_ctrl_c_should_exit,
    repl_reset_ctrl_c_gate,
)


@pytest.fixture(autouse=True)
def _isolate_ctrl_c_gate() -> Iterator[None]:
    """Reset the process-wide Ctrl+C state around every test in this module.

    ``repl_reset_ctrl_c_gate`` deliberately leaves an accepted exit standing —
    it runs during teardown in production — so the flags are cleared directly
    here. Without this an exit accepted in one test makes the next test's first
    press raise and aborts the pytest session.
    """
    _restore()
    yield
    _restore()


def _restore() -> None:
    repl_reset_ctrl_c_gate()
    _exit_in_progress[0] = False
    _exit_interrupted[0] = False


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


def test_ctrl_c_during_shell_teardown_does_not_ask_for_a_second_press(capsys) -> None:
    """Once the REPL accepts the exit, a press interrupts teardown instead of re-arming.

    The shell leaves the prompt and then blocks for seconds on final
    persistence, so SIGINT reaches the process handler again. It used to start
    its own double-press dance there — "(Press Ctrl+C again to exit)", then
    "Goodbye!" — for an exit the user had already confirmed.
    """
    assert repl_prompt_ctrl_c_should_exit() is False
    assert repl_prompt_ctrl_c_should_exit() is True
    capsys.readouterr()

    with pytest.raises(KeyboardInterrupt):
        handle_ctrl_c_press()

    assert capsys.readouterr().out == ""
    assert ctrl_c_exit_interrupted() is True


def test_ctrl_c_exit_from_the_signal_handler_also_arms_teardown(capsys) -> None:
    """The handler's own confirmed exit runs the same teardown, so arm it there too."""
    handle_ctrl_c_press()
    assert "(Press Ctrl+C again to exit)" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        handle_ctrl_c_press()
    assert "Goodbye" in capsys.readouterr().out

    with pytest.raises(KeyboardInterrupt):
        handle_ctrl_c_press()
    assert capsys.readouterr().out == ""


def test_resetting_the_gate_keeps_an_accepted_exit(capsys) -> None:
    """The prompt re-arms its gate on the way out; that must not undo the exit.

    The Ctrl+C binding ends the prompt with an empty result, which the reader
    treats as an accepted line and follows with ``repl_reset_ctrl_c_gate``.
    Controller cleanup runs after that and can block, so the exit has to stand.
    """
    assert repl_prompt_ctrl_c_should_exit() is False
    assert repl_prompt_ctrl_c_should_exit() is True

    repl_reset_ctrl_c_gate()
    capsys.readouterr()

    with pytest.raises(KeyboardInterrupt):
        handle_ctrl_c_press()
    assert capsys.readouterr().out == ""
