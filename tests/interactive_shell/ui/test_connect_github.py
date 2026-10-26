"""Mandatory GitHub screen: the open/continue/exit gate loop."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from surfaces.interactive_shell.ui import connect_github
from surfaces.interactive_shell.ui.connect_github import (
    ConnectGithubChoice,
    run_connect_github_gate,
)

_URL = "https://app.opensre.test/home"


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=100), buf


def _scripted_menu(
    monkeypatch: pytest.MonkeyPatch, picks: list[ConnectGithubChoice | None]
) -> list[ConnectGithubChoice]:
    """Answer the menu from ``picks``; return the option focused on each round."""
    focused: list[ConnectGithubChoice] = []

    def _prompt(*, focus: ConnectGithubChoice) -> ConnectGithubChoice | None:
        focused.append(focus)
        return picks.pop(0)

    monkeypatch.setattr(connect_github, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(connect_github, "prompt_connect_github", _prompt)
    return focused


def test_the_shell_waits_until_the_app_confirms_github(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: open the dashboard, continue too early, then continue once connected.
    console, buf = _console()
    focused = _scripted_menu(
        monkeypatch,
        [ConnectGithubChoice.OPEN, ConnectGithubChoice.CHECK, ConnectGithubChoice.CHECK],
    )
    answers = iter([False, True])
    opened: list[bool] = []

    # Act
    proceed = run_connect_github_gate(
        console,
        is_connected=lambda: False,
        recheck=lambda: next(answers),
        open_dashboard=lambda: opened.append(True),
        dashboard_url=_URL,
    )

    # Assert: only the app's confirmation lets the shell start.
    assert proceed is True
    assert opened == [True]
    assert focused == [
        ConnectGithubChoice.OPEN,
        ConnectGithubChoice.CHECK,
        ConnectGithubChoice.CHECK,
    ]
    out = buf.getvalue()
    assert connect_github.CONNECT_GITHUB_TITLE in out
    assert "GitHub is not connected yet" in out


@pytest.mark.parametrize("pick", [ConnectGithubChoice.EXIT, None])
def test_leaving_the_screen_never_starts_the_shell(
    monkeypatch: pytest.MonkeyPatch, pick: ConnectGithubChoice | None
) -> None:
    console, _buf = _console()
    _scripted_menu(monkeypatch, [pick])

    proceed = run_connect_github_gate(
        console,
        is_connected=lambda: False,
        recheck=lambda: pytest.fail("leaving must not ask the app"),
        open_dashboard=lambda: pytest.fail("leaving must not open the browser"),
        dashboard_url=_URL,
    )

    assert proceed is False


def test_a_connected_member_sees_no_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    console, buf = _console()
    monkeypatch.setattr(
        connect_github,
        "prompt_connect_github",
        lambda **_k: pytest.fail("no menu for a connected member"),
    )

    proceed = run_connect_github_gate(
        console,
        is_connected=lambda: True,
        recheck=lambda: False,
        open_dashboard=lambda: None,
        dashboard_url=_URL,
    )

    assert proceed is True
    assert buf.getvalue() == ""
