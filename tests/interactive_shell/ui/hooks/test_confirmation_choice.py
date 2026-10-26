"""The confirmation hook renders the state's rows and delivers a pick.

Rows come from ``ReplState.confirm_options`` (Yes/No, or the three-row auto-level
gate with an "always" row). ↑/↓, Enter, row tags, digits, and the ``y``/``n``
answer keys all resolve to one of those rows; the default selection is the last
(cancel) row so a stray Enter aborts.
"""

from __future__ import annotations

import re
import threading

from surfaces.interactive_shell.runtime.core.state import ReplState
from surfaces.interactive_shell.ui.hooks import (
    confirmation_choice_overlay_ansi,
    install_confirmation_key_bindings,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_YES_NO = (("y", "Yes, allow"), ("n", "No, cancel"))
_THREE = (
    ("y", "Yes, allow"),
    ("always", "Yes, and always allow reversible commands"),
    ("n", "No, cancel"),
)


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


class _FakeState:
    def __init__(self, options: tuple[tuple[str, str], ...]) -> None:
        self.confirm_options = options
        self.confirm_selected = len(options) - 1
        self.delivered: list[str] = []

    def is_awaiting_confirmation(self) -> bool:
        return True

    def deliver_confirmation(self, answer: str) -> None:
        self.delivered.append(answer)


def test_overlay_marks_the_selected_row_with_the_arrow() -> None:
    yes = _plain(confirmation_choice_overlay_ansi(0, _YES_NO))
    no = _plain(confirmation_choice_overlay_ansi(1, _YES_NO))

    assert "❯ [a] Yes, allow" in yes and "❯ [b] No, cancel" not in yes
    assert "❯ [b] No, cancel" in no and "❯ [a] Yes, allow" not in no


def test_overlay_renders_three_tagged_rows_for_the_auto_gate() -> None:
    rendered = _plain(confirmation_choice_overlay_ansi(1, _THREE))
    assert "[a] Yes, allow" in rendered
    assert "❯ [b] Yes, and always allow reversible commands" in rendered
    assert "[c] No, cancel" in rendered


def test_overlay_fits_inside_a_narrow_prompt(monkeypatch) -> None:
    """A prompt narrower than a typical option label must not wrap the box."""
    width = 20
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.confirmation_choice.prompt_line_width",
        lambda: width,
    )
    rendered = _plain(confirmation_choice_overlay_ansi(0, _YES_NO))
    for line in rendered.splitlines():
        assert len(line) <= width
    assert "❯ [a]" in rendered
    assert "[b]" in rendered


def test_letter_keys_deliver_the_matching_answer() -> None:
    state = _FakeState(_YES_NO)
    bindings = install_confirmation_key_bindings(state, lambda: None)
    by_key = {str(b.keys[0]): b.handler for b in bindings.bindings}

    by_key["n"](None)
    by_key["y"](None)
    assert state.delivered == ["n", "y"]


def test_arrow_keys_move_and_wrap_then_enter_delivers_the_row() -> None:
    state = _FakeState(_THREE)  # starts on the last row (No)
    redraws: list[bool] = []
    bindings = install_confirmation_key_bindings(state, lambda: redraws.append(True))
    by_key = {str(b.keys[0]): b.handler for b in bindings.bindings}

    by_key["Keys.Down"](None)  # No(2) -> wrap Yes(0)
    assert state.confirm_selected == 0
    by_key["Keys.Up"](None)  # Yes(0) -> wrap No(2)
    assert state.confirm_selected == 2
    by_key["Keys.Down"](None)  # No(2) -> Yes(0)
    by_key["Keys.Down"](None)  # Yes(0) -> always(1)
    assert state.confirm_selected == 1
    by_key["Keys.ControlM"](None)
    assert state.delivered == ["always"]
    assert redraws == [True, True, True, True]


def test_digit_and_tag_keys_pick_a_row_directly() -> None:
    state = _FakeState(_THREE)
    bindings = install_confirmation_key_bindings(state, lambda: None)
    by_key = {str(b.keys[0]): b.handler for b in bindings.bindings}

    by_key["2"](None)  # second row = always
    by_key["c"](None)  # third row tag = No
    assert state.delivered == ["always", "n"]


def test_enter_on_default_selection_cancels() -> None:
    # begin_confirmation defaults the arrow to the last row (cancel) so a stray
    # Enter aborts instead of approving.
    state = ReplState()
    state.begin_confirmation(threading.Event(), "Proceed?")
    assert state.confirm_selected == 1  # Yes/No default -> No is last

    bindings = install_confirmation_key_bindings(state, lambda: None)
    by_key = {str(b.keys[0]): b.handler for b in bindings.bindings}
    by_key["Keys.ControlM"](None)
    assert state.confirm_response == ["n"]
