from __future__ import annotations

import questionary
from prompt_toolkit.input.defaults import create_pipe_input  # type: ignore[import-not-found]
from prompt_toolkit.output import DummyOutput  # type: ignore[import-not-found]

from app.cli.prompt_support import install_questionary_escape_cancel


def test_install_questionary_escape_cancel_is_idempotent() -> None:
    install_questionary_escape_cancel()
    first = questionary.select
    install_questionary_escape_cancel()
    assert questionary.select is first


def test_stock_questionary_select_escape_cancels() -> None:
    install_questionary_escape_cancel()
    q = questionary.select("Pick", choices=["a", "b"])
    with create_pipe_input() as pipe_input:
        pipe_input.send_bytes(b"\x1b")
        app = q.application
        app.input = pipe_input
        app.output = DummyOutput()
        assert app.run() is None
