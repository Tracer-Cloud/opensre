from __future__ import annotations

import time

import pytest
import questionary
from prompt_toolkit.input.defaults import create_pipe_input  # type: ignore[import-not-found]
from prompt_toolkit.output import DummyOutput  # type: ignore[import-not-found]

from app.cli.prompt_support import (
    _last_ctrl_c,
    handle_ctrl_c_press,
    install_questionary_ctrl_c_double_exit,
    install_questionary_escape_cancel,
)


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


def test_install_questionary_ctrl_c_double_exit_is_idempotent() -> None:
    install_questionary_ctrl_c_double_exit()
    first = questionary.select
    install_questionary_ctrl_c_double_exit()
    assert questionary.select is first


def test_ctrl_c_first_press_shows_hint_and_reprompts(capsys) -> None:
    """First Ctrl+C prints the hint and re-displays the prompt; Enter then submits."""
    _last_ctrl_c[0] = 0.0
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
    _last_ctrl_c[0] = 0.0  # effectively "long ago"
    handle_ctrl_c_press()
    out = capsys.readouterr().out
    assert "(Press Ctrl+C again to exit)" in out
