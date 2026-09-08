"""Mandatory account screen: rendering and the sign-in/exit gate loop."""

from __future__ import annotations

import io

from rich.console import Console

from config.constants import SIGN_IN_PROMPT, WELCOME_DESCRIPTION, WELCOME_TITLE
from surfaces.interactive_shell.ui import sign_in
from surfaces.interactive_shell.ui.sign_in import (
    SignInChoice,
    render_sign_in_screen,
    run_sign_in_gate,
)


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=100), buf


def test_screen_shows_welcome_box_and_sign_in_prompt() -> None:
    # Arrange / Act
    console, buf = _console()
    render_sign_in_screen(console)

    # Assert: product copy is present; the banner logo/status renders alongside it.
    out = buf.getvalue()
    assert WELCOME_TITLE in out
    assert WELCOME_DESCRIPTION.split(" that ")[0] in out  # description body reached the screen
    assert SIGN_IN_PROMPT in out
    assert "Skills" in out and "Integrations" in out


def test_welcome_title_renders_in_the_accent_colour() -> None:
    from infrastructure.terminal.theme import HIGHLIGHT

    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        no_color=False,
    )
    console.print(sign_in.build_welcome_box())
    raw = buf.getvalue()
    hex_color = str(HIGHLIGHT).lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    assert f"{r};{g};{b}" in raw  # blue title in the accent colour


def test_gate_proceeds_immediately_when_already_signed_in(monkeypatch) -> None:
    # No screen, no menu when the user is already signed in.
    rendered: list[bool] = []
    monkeypatch.setattr(sign_in, "render_sign_in_screen", lambda _c: rendered.append(True))
    console, _ = _console()

    result = run_sign_in_gate(console, is_signed_in=lambda: True, login=lambda: False)

    assert result is True
    assert rendered == []


def test_gate_fails_closed_on_non_interactive_stdin(monkeypatch) -> None:
    monkeypatch.setattr(sign_in, "repl_tty_interactive", lambda: False)
    console, output = _console()

    result = run_sign_in_gate(console, is_signed_in=lambda: False, login=lambda: False)

    assert result is False
    assert "opensre account login" in output.getvalue()


def test_gate_logs_in_then_proceeds(monkeypatch) -> None:
    login_calls: list[bool] = []
    monkeypatch.setattr(sign_in, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(sign_in, "render_sign_in_screen", lambda _c: None)
    monkeypatch.setattr(sign_in, "prompt_login_or_exit", lambda **_kwargs: SignInChoice.LOGIN)
    console, _ = _console()

    def _login() -> bool:
        login_calls.append(True)
        return True

    result = run_sign_in_gate(console, is_signed_in=lambda: False, login=_login)

    assert result is True
    assert login_calls == [True]


def test_gate_exit_declines(monkeypatch) -> None:
    monkeypatch.setattr(sign_in, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(sign_in, "render_sign_in_screen", lambda _c: None)
    monkeypatch.setattr(sign_in, "prompt_login_or_exit", lambda **_kwargs: SignInChoice.EXIT)
    console, _ = _console()

    result = run_sign_in_gate(console, is_signed_in=lambda: False, login=lambda: False)

    assert result is False


def test_signed_out_choice_is_explicit() -> None:
    assert SignInChoice.EXIT.value == "Exit and stay signed out"


def test_gate_retries_after_a_failed_login_then_exits(monkeypatch) -> None:
    # Login fails once, then the user picks Exit — the gate re-prompts, does not loop forever.
    choices = iter([SignInChoice.LOGIN, SignInChoice.EXIT])
    monkeypatch.setattr(sign_in, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(sign_in, "render_sign_in_screen", lambda _c: None)
    monkeypatch.setattr(sign_in, "prompt_login_or_exit", lambda **_kwargs: next(choices))
    console, _ = _console()

    result = run_sign_in_gate(console, is_signed_in=lambda: False, login=lambda: False)

    assert result is False


def test_gate_enters_the_shell_on_a_configured_provider_without_signing_in(monkeypatch) -> None:
    # The only path to a local model: an account session pins the shell to the hosted route.
    # A rejected or unreachable session leaves that route on disk, so picking the
    # local model must drop it or the shell keeps calling the hosted proxy.
    dropped: list[bool] = []
    monkeypatch.setattr(sign_in, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(sign_in, "render_sign_in_screen", lambda _c: None)
    monkeypatch.setattr(sign_in, "prompt_login_or_exit", lambda **_kwargs: SignInChoice.OWN_MODEL)
    console, _ = _console()

    def _on_own_provider() -> None:
        dropped.append(True)

    result = run_sign_in_gate(
        console,
        is_signed_in=lambda: False,
        login=lambda: False,
        has_own_provider=lambda: True,
        on_own_provider=_on_own_provider,
    )

    assert result is True
    assert dropped == [True]


def test_menu_hides_the_own_model_row_without_a_configured_provider(monkeypatch) -> None:
    # A fresh install keeps the two-way gate: no provider means no way past sign-in.
    offered: list[list[str]] = []

    def _choose(*, choices: list[tuple[str, str]], **_kwargs: object) -> None:
        offered.append([value for value, _label in choices])
        return None

    monkeypatch.setattr(sign_in, "repl_choose_one", _choose)

    sign_in.prompt_login_or_exit(offer_own_model=False)
    sign_in.prompt_login_or_exit(offer_own_model=True)

    assert offered[0] == [SignInChoice.LOGIN, SignInChoice.EXIT]
    assert SignInChoice.OWN_MODEL in offered[1]
