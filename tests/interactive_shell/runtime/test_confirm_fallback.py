"""When the prompt app is suspended (subprocess turns), confirmation reads a
plain line instead of parking on the hidden arrow-nav — otherwise it hangs while
the cooked terminal echoes the arrow keys.
"""

from __future__ import annotations

import builtins

import pytest

from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.runtime.turn_host import (
    AgentTurnResources,
    _confirm_via_prompt,
    _confirm_via_readline,
)
from surfaces.interactive_shell.session import Session

_THREE = (
    ("y", "Yes, allow"),
    ("always", "Yes, and always allow reversible commands"),
    ("n", "No, cancel"),
)


class _FakeApp:
    def __init__(self, *, running: bool) -> None:
        self.is_running = running


def _runtime(*, running: bool | None, exclusive: bool) -> AgentTurnResources:
    session = Session()
    session.terminal.prompt_app = None if running is None else _FakeApp(running=running)
    session.terminal.exclusive_stdin_active = exclusive
    session.terminal.pending_confirm_options = _THREE
    return AgentTurnResources(
        session=session,
        state=ReplState(),
        spinner=SpinnerState(),
        invalidate_prompt=lambda: None,
    )


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("a", "y"),  # row tag
        ("2", "always"),  # digit
        ("n", "n"),  # answer key
        ("y", "y"),
        ("weird", "weird"),  # passthrough for the gate to interpret
    ],
)
def test_readline_maps_tags_digits_and_answers(monkeypatch, typed: str, expected: str) -> None:
    monkeypatch.setattr(builtins, "input", lambda _prompt: typed)
    assert _confirm_via_readline("Approve?", _THREE) == expected


def test_readline_empty_enter_cancels_like_the_arrow_nav_default(monkeypatch) -> None:
    # Arrow-nav defaults the selection to the last row (cancel). A stray Enter
    # on the cooked fallback must not approve.
    monkeypatch.setattr(builtins, "input", lambda _prompt: "  ")
    assert _confirm_via_readline("Approve?", _THREE) == "n"


def test_readline_treats_interrupt_as_cancel(monkeypatch) -> None:
    def _raise(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", _raise)
    assert _confirm_via_readline("Approve?", _THREE) == "n"


def test_readline_defaults_to_yes_no_without_options(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(builtins, "input", lambda prompt: captured.append(prompt) or "y")
    assert _confirm_via_readline("Approve?", None) == "y"
    # Two default rows -> a two-tag prompt.
    assert captured == ["Approve? [a/b] "]


def _stub_confirm_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.turn_host._confirm_via_readline",
        lambda *_args, **_kwargs: "readline",
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.turn_host.request_confirmation_via_prompt",
        lambda *_args, **_kwargs: "arrow",
    )


def test_confirm_via_prompt_uses_readline_when_exclusive_stdin_owns_the_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The live prompt app can still report is_running during an exclusive-stdin
    # turn; parking on it hangs. Exclusive stdin must force the cooked path.
    _stub_confirm_paths(monkeypatch)
    runtime = _runtime(running=True, exclusive=True)
    assert _confirm_via_prompt(runtime, "Approve?") == "readline"
    assert runtime.session.terminal.pending_confirm_options is None


def test_confirm_via_prompt_uses_readline_when_the_prompt_app_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_confirm_paths(monkeypatch)
    assert _confirm_via_prompt(_runtime(running=False, exclusive=False), "Approve?") == "readline"
    assert _confirm_via_prompt(_runtime(running=None, exclusive=False), "Approve?") == "readline"


def test_confirm_via_prompt_uses_arrow_nav_when_the_live_prompt_owns_the_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_confirm_paths(monkeypatch)
    assert _confirm_via_prompt(_runtime(running=True, exclusive=False), "Approve?") == "arrow"
