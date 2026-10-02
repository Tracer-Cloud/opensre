"""Esc on the live prompt: only a deliberate press cancels a running turn.

prompt-toolkit parses one read into one batch of keys, so a terminal reply or
an Option chord reaches the key processor as Escape with its tail queued behind
it. Neither may cancel the turn, clear an idle draft, or act as keystrokes.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.application import create_app_session
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import PipeInput, create_pipe_input
from prompt_toolkit.output import DummyOutput

from surfaces.interactive_shell.runtime.core.state import ReplState
from surfaces.interactive_shell.ui.hooks import (
    install_confirmation_key_bindings,
    install_plan_expand_key_bindings,
)
from surfaces.interactive_shell.ui.input_prompt import build_prompt_session
from surfaces.interactive_shell.ui.input_prompt.key_bindings import (
    build_cancel_key_bindings,
    install_session_key_bindings,
)

# ``ttimeoutlen`` for these prompts: how long a lone ESC waits before it is
# flushed as the Esc key (0.5 s by default).
_ESC_FLUSH_S = 0.05


@pytest.fixture(autouse=True)
def _interactive_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")


@dataclass
class _LivePrompt:
    prompt: PromptSession[str]
    keys: PipeInput
    state: ReplState
    handled: list[object]

    @property
    def text(self) -> str:
        return self.prompt.default_buffer.text

    def turn_cancelled(self) -> bool:
        event = self.state.current_cancel_event
        return event is not None and event.is_set()


async def _until(predicate: Callable[[], bool]) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the prompt never reached the expected state")


@asynccontextmanager
async def _live_prompt(*, turn_running: bool) -> AsyncIterator[_LivePrompt]:
    """A rendered prompt with the shell's cancel and plan bindings, optionally mid-turn."""
    with (
        create_pipe_input() as keys,
        create_app_session(input=keys, output=DummyOutput()),
    ):
        state = ReplState()
        turn = asyncio.create_task(asyncio.sleep(3600))
        if turn_running:
            state.attach_turn_task(turn)
        prompt = build_prompt_session()
        prompt.history = InMemoryHistory()
        prompt.default_buffer.history = prompt.history
        install_session_key_bindings(prompt, build_cancel_key_bindings(state))
        install_session_key_bindings(
            prompt, install_plan_expand_key_bindings(state, lambda: True, lambda: None)
        )
        prompt.app.ttimeoutlen = _ESC_FLUSH_S
        rendered = asyncio.Event()
        handled: list[object] = []

        def _on_render(_app: object) -> None:
            rendered.set()

        prompt.app.after_render += _on_render
        prompt.app.key_processor.after_key_press += handled.append
        reading = asyncio.create_task(prompt.prompt_async("> "))
        try:
            await asyncio.wait_for(rendered.wait(), timeout=5)
            yield _LivePrompt(prompt=prompt, keys=keys, state=state, handled=handled)
        finally:
            if prompt.app.is_running and not prompt.app.is_done:
                prompt.app.exit(result="")
            await asyncio.wait_for(reading, timeout=5)
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        pytest.param("\x1b[I\x1b[O", id="focus-reports"),
        pytest.param("\x1b[?62;4c", id="da1"),
        pytest.param("\x1b[?2026;2$y", id="decrqm"),
        pytest.param("\x1b]11;rgb:1e1e/1e1e/1e1e\x1b\\", id="osc11"),
    ],
)
async def test_terminal_replies_neither_cancel_a_turn_nor_reach_the_prompt(reply: str) -> None:
    async with _live_prompt(turn_running=True) as live:
        live.keys.send_text(f"{reply}#")  # "#" marks the end of the batch
        await _until(lambda: live.text.endswith("#"))

        assert live.text == "#"
        assert not live.turn_cancelled()


@pytest.mark.asyncio
async def test_unbound_option_chord_types_its_key_without_cancelling() -> None:
    async with _live_prompt(turn_running=True) as live:
        live.keys.send_text("\x1bz#")
        await _until(lambda: live.text.endswith("#"))

        assert live.text == "z#"
        assert not live.turn_cancelled()


@pytest.mark.asyncio
async def test_option_bracket_flushed_after_the_timeout_does_not_cancel() -> None:
    # ``ESC [`` is a CPR prefix, so the parser holds it until ``ttimeoutlen``
    # and then flushes Escape and "[" together.
    async with _live_prompt(turn_running=True) as live:
        live.keys.send_text("\x1b[")
        await _until(lambda: len(live.handled) >= 2)

        assert not live.turn_cancelled()


@pytest.mark.asyncio
async def test_option_p_toggles_the_plan_instead_of_cancelling() -> None:
    # The eager Esc binding used to win before ``escape p`` could match.
    async with _live_prompt(turn_running=True) as live:
        live.keys.send_text("\x1bp")
        await _until(lambda: live.state.plan_expanded)

        assert not live.turn_cancelled()
        assert live.text == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "keys",
    [
        pytest.param("\x1b", id="lone-esc"),
        pytest.param("\x1b\x1b[12;1R", id="esc-read-with-a-cpr-reply"),
    ],
)
async def test_deliberate_escape_cancels_a_running_turn(keys: str) -> None:
    async with _live_prompt(turn_running=True) as live:
        live.keys.send_text(keys)

        await _until(live.turn_cancelled)


@pytest.mark.asyncio
async def test_stray_reply_keeps_an_idle_draft() -> None:
    async with _live_prompt(turn_running=False) as live:
        live.keys.send_text("draft")
        await _until(lambda: live.text == "draft")

        live.keys.send_text("\x1b[?62;4c#")
        await _until(lambda: live.text.endswith("#"))

        assert live.text == "draft#"


@pytest.mark.asyncio
async def test_stray_reply_never_answers_a_pending_confirmation() -> None:
    # xterm's DA1 reply carries "1", the key for the first row ("Yes, allow").
    # Before the Esc fix it cancelled the turn; it must not approve instead.
    async with _live_prompt(turn_running=True) as live:
        install_session_key_bindings(
            live.prompt, install_confirmation_key_bindings(live.state, lambda: None)
        )
        live.state.begin_confirmation(threading.Event(), "Run it?")

        live.keys.send_text("\x1b[?1;2c#")
        await _until(lambda: live.text.endswith("#"))

        assert live.state.confirm_response == []
        assert live.state.is_awaiting_confirmation()
        assert not live.turn_cancelled()


@pytest.mark.asyncio
async def test_terminal_reply_leaves_the_command_tray_open() -> None:
    async with _live_prompt(turn_running=True) as live:
        live.keys.send_text("/")
        await _until(lambda: live.prompt.default_buffer.complete_state is not None)
        handled = len(live.handled)

        live.keys.send_text("\x1b[?62;4c")
        await _until(lambda: len(live.handled) >= handled + 2)  # Escape and "["

        assert live.prompt.default_buffer.complete_state is not None
        assert live.text == "/"
        assert not live.turn_cancelled()
