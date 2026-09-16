"""Arrow-navigable choice hook for the execution-confirmation gate.

Renders the pending confirmation as a stacked, tagged choice (``[a] …`` /
``[b] …`` / …) driven with ↑/↓ and Enter — or the row's letter, the ``y``/``n``
answer keys, or a digit — instead of a typed answer. The free-text box is
hidden while a confirmation is active (see ``typing_box_hidden``), so the choice
cannot be confused with a message. The options are supplied per confirmation by
``ReplState.confirm_options``. Selecting delivers the answer to the parked turn worker.
"""

from __future__ import annotations

from typing import Protocol

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent

from infrastructure.terminal import theme as ui_theme
from surfaces.shared.terminal.prompt_layout import prompt_line_width

# Rows are supplied per confirmation via ``ReplState.confirm_options``.
_MAX_TAGGED = 9
# Leave room for the ``│ `` / ` │`` box chrome plus one spare column so the
# border never reaches the last cell and soft-wraps.
_BOX_CHROME = 5


class ConfirmChoiceState(Protocol):
    """The subset of ``ReplState`` this hook drives."""

    confirm_selected: int
    confirm_options: tuple[tuple[str, str], ...]

    def is_awaiting_confirmation(self) -> bool:
        """True while a confirmation is pending and owns the keyboard."""

    def deliver_confirmation(self, answer: str) -> None:
        """Hand ``answer`` to the parked turn worker and wake it."""


def confirmation_choice_overlay_ansi(selected: int, options: tuple[tuple[str, str], ...]) -> str:
    """Bordered ``❯ [a] …`` rows for ``options``, accenting the selected one.

    The options sit inside a dim box (like the composer frame) so the pending
    decision reads as its own panel; the selected row carries the accent arrow.
    """
    if not options:
        return ""
    index = selected % len(options)
    contents = [
        f"{'❯' if position == index else ' '} [{chr(ord('a') + position)}] {label}"
        for position, (_answer, label) in enumerate(options)
    ]
    # Span the available prompt width (like the composer box below), not the content.
    inner = max(0, prompt_line_width() - _BOX_CHROME)
    border = ui_theme.PROMPT_FRAME_ANSI
    reset = ui_theme.ANSI_RESET
    lines = [f"{border}┌{'─' * (inner + 2)}┐{reset}"]
    for position, content in enumerate(contents):
        style = ui_theme.PROMPT_ACCENT_ANSI if position == index else ui_theme.DIM_COUNTER_ANSI
        padded = content[:inner].ljust(inner)
        lines.append(f"{border}│{reset} {style}{padded}{reset} {border}│{reset}")
    lines.append(f"{border}└{'─' * (inner + 2)}┘{reset}")
    return "\n".join(lines)


def install_confirmation_key_bindings(state: ConfirmChoiceState, redraw: object) -> KeyBindings:
    """Bindings active only while awaiting confirmation: ↑/↓ move, Enter/letters/digits pick.

    ``redraw`` invalidates the prompt so a selection change repaints at once.
    The handlers read ``state.confirm_options`` at press time, so a confirmation
    with two or three rows is driven by the same fixed bindings.
    """
    kb = KeyBindings()
    awaiting = Condition(state.is_awaiting_confirmation)

    def _deliver_index(index: int) -> None:
        options = state.confirm_options
        if 0 <= index < len(options):
            state.deliver_confirmation(options[index][0])

    def _deliver_answer(answer: str) -> None:
        for option_answer, _label in state.confirm_options:
            if option_answer == answer:
                state.deliver_confirmation(answer)
                return

    def _move(delta: int) -> None:
        count = len(state.confirm_options) or 1
        state.confirm_selected = (state.confirm_selected + delta) % count
        if callable(redraw):
            redraw()

    @kb.add("up", filter=awaiting, eager=True)
    def _on_up(_event: KeyPressEvent) -> None:
        _move(-1)

    @kb.add("down", filter=awaiting, eager=True)
    def _on_down(_event: KeyPressEvent) -> None:
        _move(1)

    @kb.add("c-m", filter=awaiting, eager=True)
    def _on_enter(_event: KeyPressEvent) -> None:
        _deliver_index(state.confirm_selected)

    # Row tags [a]..[i] and digits 1..9 pick that row directly.
    for position in range(_MAX_TAGGED):
        for key in (chr(ord("a") + position), str(position + 1)):

            @kb.add(key, filter=awaiting, eager=True)
            def _on_row_key(_event: KeyPressEvent, _index: int = position) -> None:
                _deliver_index(_index)

    # y / n stay as answer shortcuts for the common allow / cancel rows.
    for answer in ("y", "n"):

        @kb.add(answer, filter=awaiting, eager=True)
        def _on_answer_key(_event: KeyPressEvent, _answer: str = answer) -> None:
            _deliver_answer(_answer)

    return kb


__all__ = [
    "ConfirmChoiceState",
    "confirmation_choice_overlay_ansi",
    "install_confirmation_key_bindings",
]
