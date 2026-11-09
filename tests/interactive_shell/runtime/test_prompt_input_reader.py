"""Tests for prompt input event conversion."""

from __future__ import annotations

import asyncio
import io
import threading
from collections.abc import Callable
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from surfaces.interactive_shell.runtime.core.prompt_builder import PromptBuilder
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.runtime.input import (
    InputCancelled,
    InputClosed,
    InputSubmitted,
    PromptInputReader,
)
from surfaces.interactive_shell.runtime.input import prompt_input_reader as reader_module
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.input_prompt import build_prompt_session


class FakePrompt:
    def __init__(self, read: Callable[[], str]) -> None:
        self._read = read

    async def read_prompt_text(self) -> str:
        return self._read()


class SequencePrompt:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    async def read_prompt_text(self) -> str:
        return self._values.pop(0)


def _reader(
    prompt: Any,
    state: ReplState | None = None,
    session: Session | None = None,
    console: Console | None = None,
) -> PromptInputReader:
    return PromptInputReader(
        prompt,
        state or ReplState(),
        session or Session(),
        console or Console(file=io.StringIO(), force_terminal=False),
    )


def _running_state() -> tuple[ReplState, asyncio.Task[None]]:
    state = ReplState()
    task = asyncio.create_task(asyncio.sleep(60))
    state.start_dispatch(task=task, cancel_event=threading.Event())
    return state, task


@pytest.mark.asyncio
async def test_prompt_input_reader_submits_normal_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_calls = 0

    def _reset() -> None:
        nonlocal reset_calls
        reset_calls += 1

    monkeypatch.setattr(reader_module, "repl_reset_ctrl_c_gate", _reset)

    event = await _reader(FakePrompt(lambda: "show incidents")).read()

    assert event == InputSubmitted("show incidents")
    assert reset_calls == 1


@pytest.mark.asyncio
async def test_prompt_input_reader_eof_with_dispatch_running_closes_the_shell() -> None:
    # Ctrl-D is swallowed while a turn runs, so EOF mid-turn is a closed
    # terminal, not a request to cancel the turn and keep reading.
    state, task = _running_state()
    try:
        event = await _reader(FakePrompt(lambda: (_ for _ in ()).throw(EOFError)), state).read()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert event == InputClosed()


@pytest.mark.asyncio
async def test_ctrl_d_ends_the_shell_only_from_an_idle_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    state, task = _running_state()
    with (
        create_pipe_input() as keys,
        create_app_session(input=keys, output=DummyOutput()),
    ):
        session = Session()
        pt_session = build_prompt_session(session)
        pt_session.history = InMemoryHistory()
        pt_session.default_buffer.history = pt_session.history
        builder = PromptBuilder(session, state, SpinnerState(), pt_session)
        builder.setup()
        reader = _reader(builder, state, session)
        try:
            # Mid-turn on an empty prompt: Ctrl-D neither cancels nor closes,
            # and the prompt keeps taking input.
            keys.send_text("\x04next\r")
            assert await asyncio.wait_for(reader.read(), timeout=5) == InputSubmitted("next")
            assert state.current_cancel_event is not None
            assert not state.current_cancel_event.is_set()

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            keys.send_text("\x04")
            assert await asyncio.wait_for(reader.read(), timeout=5) == InputClosed()
        finally:
            await builder.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_prompt_input_reader_eof_without_dispatch_renders_resume_hint() -> None:
    output = io.StringIO()
    session = Session()
    session.session_id = "session-123"
    console = Console(file=output, force_terminal=False, color_system=None)

    event = await _reader(
        FakePrompt(lambda: (_ for _ in ()).throw(EOFError)),
        session=session,
        console=console,
    ).read()

    assert event == InputClosed()
    assert "Resume this session with:" in output.getvalue()
    assert "/resume session-123" in output.getvalue()
    assert "--resume session-123" in output.getvalue()
    assert "Goodbye!" in output.getvalue()


@pytest.mark.asyncio
async def test_prompt_input_reader_keyboard_interrupt_with_dispatch_running_returns_cancelled() -> (
    None
):
    state, task = _running_state()
    try:
        event = await _reader(
            FakePrompt(lambda: (_ for _ in ()).throw(KeyboardInterrupt)),
            state,
        ).read()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert event == InputCancelled()


@pytest.mark.asyncio
async def test_prompt_input_reader_keyboard_interrupt_without_dispatch_uses_ctrl_c_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reader_module, "repl_prompt_ctrl_c_should_exit", lambda: False)

    state = ReplState()
    event = await _reader(
        FakePrompt(lambda: (_ for _ in ()).throw(KeyboardInterrupt)),
        state,
    ).read()

    assert event == InputCancelled()
    assert state.is_ctrl_c_exit_hint_visible()


@pytest.mark.asyncio
async def test_prompt_input_reader_keyboard_interrupt_without_dispatch_can_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reader_module, "repl_prompt_ctrl_c_should_exit", lambda: True)

    event = await _reader(FakePrompt(lambda: (_ for _ in ()).throw(KeyboardInterrupt))).read()

    assert event == InputClosed()


@pytest.mark.asyncio
async def test_prompt_input_reader_strips_cpr_sequences() -> None:
    event = await _reader(FakePrompt(lambda: "status\x1b[12;80R now")).read()

    assert event == InputSubmitted("status now")


@pytest.mark.asyncio
async def test_prompt_input_reader_ignores_cpr_only_input() -> None:
    event = await _reader(
        SequencePrompt(["\x1b[12;80R", "show status"]),
    ).read()

    assert event == InputSubmitted("show status")


@pytest.mark.asyncio
async def test_prompt_input_reader_ignores_held_key_spam() -> None:
    spam = "d" * 80 + "cs"
    event = await _reader(SequencePrompt([spam, "real ask"])).read()
    assert event == InputSubmitted("real ask")
