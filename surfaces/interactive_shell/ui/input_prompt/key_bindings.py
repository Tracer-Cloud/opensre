"""Prompt-toolkit key bindings for the REPL prompt."""

from __future__ import annotations

from typing import Protocol

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.filters import has_completions
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys

from infrastructure.terminal.prompt_support import (
    CTRL_C_DOUBLE_PRESS_WINDOW_S,
    repl_prompt_ctrl_c_should_exit,
)


class _DispatchCancelState(Protocol):
    def is_dispatch_running(self) -> bool:
        raise NotImplementedError

    def cancel_current_dispatch(self) -> None:
        raise NotImplementedError

    def request_exit(self) -> None:
        """Request an orderly exit from the interactive shell."""

    def arm_ctrl_c_exit_hint(self, duration_seconds: float) -> None:
        """Show the transient double-press exit hint."""

    def clear_ctrl_c_exit_hint(self) -> None:
        """Clear the transient double-press exit hint."""


# Keystroke escapes, not colour codes. Terminals use either xterm's
# modifyOtherKeys encoding or the CSI-u keyboard protocol for modified Enter.
_SHIFT_ENTER_SEQUENCE = "\x1b[27;2;13~"
_MODIFIED_ENTER_SEQUENCES = frozenset(
    {
        _SHIFT_ENTER_SEQUENCE,
        *(f"\x1b[27;{modifier};13~" for modifier in range(3, 9)),
        *(f"\x1b[13;{modifier}u" for modifier in range(2, 9)),
        "\x1b\r",
        "\x1b\n",
    }
)


def _install_modified_enter_sequences() -> None:
    """Teach prompt-toolkit's VT parser the modified Enter encodings it lacks."""
    for sequence in _MODIFIED_ENTER_SEQUENCES:
        ANSI_SEQUENCES.setdefault(sequence, Keys.ControlM)


def _tab_expand_or_menu(buffer: Buffer) -> None:
    """Apply the current completion or open the menu when several choices exist."""
    if buffer.complete_state:
        state = buffer.complete_state
        completion = state.current_completion
        if completion is None and state.completions:
            completion = state.completions[0]
        if completion is not None:
            buffer.apply_completion(completion)
        return
    if buffer.completer is None:
        return
    completions = list(
        buffer.completer.get_completions(
            buffer.document,
            CompleteEvent(completion_requested=True),
        )
    )
    if len(completions) == 1:
        buffer.apply_completion(completions[0])
    else:
        buffer.start_completion(select_first=True)


def _build_prompt_key_bindings() -> KeyBindings:
    _install_modified_enter_sequences()
    bindings = KeyBindings()

    @bindings.add("c-m")
    def _accept_turn(event: KeyPressEvent) -> None:
        if event.data in _MODIFIED_ENTER_SEQUENCES:
            event.current_buffer.newline(copy_margin=False)
            return
        if event.current_buffer.complete_state is not None:
            _tab_expand_or_menu(event.current_buffer)
        event.current_buffer.validate_and_handle()

    @bindings.add("c-j")
    def _insert_newline(event: KeyPressEvent) -> None:
        # Several terminals encode Shift+Enter as LF while plain Enter is CR.
        # Ctrl+J therefore remains a portable explicit-newline fallback too.
        event.current_buffer.newline(copy_margin=False)

    @bindings.add("tab")
    def _tab_complete(event: object) -> None:
        _tab_expand_or_menu(event.current_buffer)  # type: ignore[attr-defined]

    @bindings.add("s-tab")
    def _shift_tab_complete(event: object) -> None:
        buff = event.current_buffer  # type: ignore[attr-defined]
        if buff.complete_state:
            _move_completion(buff, -1)
        else:
            buff.start_completion(select_first=False)

    @bindings.add("down", filter=has_completions)
    def _next_completion(event: KeyPressEvent) -> None:
        _move_completion(event.current_buffer, 1)

    @bindings.add("up", filter=has_completions)
    def _previous_completion(event: KeyPressEvent) -> None:
        _move_completion(event.current_buffer, -1)

    @bindings.add("escape", filter=has_completions, eager=True)
    def _close_completions(event: KeyPressEvent) -> None:
        event.current_buffer.cancel_completion()

    return bindings


def _move_completion(buffer: Buffer, direction: int) -> None:
    """Navigate from the visibly highlighted first row without an unselected stop."""
    state = buffer.complete_state
    if state is not None and state.completions:
        index = (state.complete_index or 0) + direction
        buffer.go_to_completion(max(0, min(index, len(state.completions) - 1)))


def build_cancel_key_bindings(state: _DispatchCancelState) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-c", eager=True)
    def _on_ctrl_c(event: KeyPressEvent) -> None:
        event.current_buffer.reset()
        if state.is_dispatch_running():
            state.clear_ctrl_c_exit_hint()
            state.cancel_current_dispatch()
            event.app.invalidate()
            return
        if repl_prompt_ctrl_c_should_exit():
            state.clear_ctrl_c_exit_hint()
            state.request_exit()
            event.app.exit(result="")
            return
        state.arm_ctrl_c_exit_hint(CTRL_C_DOUBLE_PRESS_WINDOW_S)
        # Full repaint, not a diff: the transient hint replaces the idle
        # "Ready…" line in place, and the renderer's line diff can skip an
        # in-place text→text swap on that row, leaving the hint unshown.
        event.app.renderer.reset()
        event.app.invalidate()

    @kb.add("escape", eager=True)
    def _on_escape(event: KeyPressEvent) -> None:
        if event.current_buffer.complete_state is not None:
            event.current_buffer.cancel_completion()
            return
        if state.is_dispatch_running():
            state.cancel_current_dispatch()
            return
        if event.current_buffer.text:
            event.current_buffer.reset()

    @kb.add("c-l")
    def _on_ctrl_l(event: KeyPressEvent) -> None:
        event.app.renderer.clear()

    return kb


def install_session_key_bindings(pt_session: object, extra_kb: KeyBindings) -> None:
    existing = getattr(pt_session, "key_bindings", None)
    merged = merge_key_bindings([existing, extra_kb]) if existing is not None else extra_kb
    pt_session.key_bindings = merged  # type: ignore[attr-defined]
