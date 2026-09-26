"""Persistent prompt lifecycle regression tests."""

from __future__ import annotations

import asyncio
import io

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.base import Size
from prompt_toolkit.output.vt100 import Vt100_Output

from surfaces.interactive_shell.runtime.core.prompt_builder import PromptBuilder
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.input_prompt import build_prompt_session


async def _wait_until_running(builder: PromptBuilder) -> asyncio.Task[str]:
    for _ in range(100):
        task = builder._prompt_task
        if task is not None and builder.pt_app is not None and builder.pt_app.is_running:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("prompt application did not start")


def _terminal_output() -> Vt100_Output:
    return Vt100_Output(
        io.StringIO(),
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )


def test_resize_after_resume_preserves_rendered_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.history = [{"type": "slash", "text": "/resume target", "ok": True}]
    session.agent.messages = [{"role": "user", "content": "restored prompt"}]
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = object()  # type: ignore[assignment]
    clear_calls: list[bool] = []
    assert session.terminal.submitted_turn_count == 0
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.repl_clear_screen",
        lambda *, scrollback=False: clear_calls.append(scrollback),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.render_launch_banner",
        lambda *_args, **_kwargs: None,
    )

    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is False
    assert clear_calls == []


def test_resize_after_internal_picker_history_rerenders_launch_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.history = [{"type": "slash", "text": "/choose", "ok": True}]
    session.terminal.remember_idle_output("Selection cancelled — type a reply instead.")
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = object()  # type: ignore[assignment]
    clear_calls: list[bool] = []
    banner_calls: list[bool] = []
    replayed: list[str] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.repl_clear_screen",
        lambda *, scrollback=False: clear_calls.append(scrollback),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.render_launch_banner",
        lambda *_args, **_kwargs: banner_calls.append(True),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.print_repl_text",
        lambda _console, text: replayed.append(text),
    )

    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is True
    assert clear_calls == [True]
    assert banner_calls == [True]
    assert replayed == ["Selection cancelled — type a reply instead."]


@pytest.mark.asyncio
async def test_enter_submits_without_restarting_the_prompt_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    with (
        create_pipe_input() as pipe_input,
        create_app_session(
            input=pipe_input,
            output=_terminal_output(),
        ),
    ):
        session = Session()
        builder = PromptBuilder(
            session,
            ReplState(),
            SpinnerState(),
            build_prompt_session(session),
        )
        builder.setup()
        try:
            first_read = asyncio.create_task(builder.read_prompt_text())
            prompt_task = await _wait_until_running(builder)
            pipe_input.send_text("first prompt\r")

            assert await asyncio.wait_for(first_read, timeout=2) == "first prompt"
            assert builder._prompt_task is prompt_task
            assert not prompt_task.done()

            second_read = asyncio.create_task(builder.read_prompt_text())
            pipe_input.send_text("second prompt\r")

            assert await asyncio.wait_for(second_read, timeout=2) == "second prompt"
            assert builder._prompt_task is prompt_task
            assert not prompt_task.done()
        finally:
            await builder.close()


@pytest.mark.asyncio
async def test_suspend_releases_and_then_restarts_the_prompt_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    with (
        create_pipe_input() as pipe_input,
        create_app_session(
            input=pipe_input,
            output=_terminal_output(),
        ),
    ):
        session = Session()
        builder = PromptBuilder(
            session,
            ReplState(),
            SpinnerState(),
            build_prompt_session(session),
        )
        builder.setup()
        try:
            first_read = asyncio.create_task(builder.read_prompt_text())
            first_prompt_task = await _wait_until_running(builder)
            pipe_input.send_text("/help\r")
            assert await asyncio.wait_for(first_read, timeout=2) == "/help"

            await builder.suspend()
            assert first_prompt_task.done()
            assert builder._prompt_task is None

            second_read = asyncio.create_task(builder.read_prompt_text())
            second_prompt_task = await _wait_until_running(builder)
            assert second_prompt_task is not first_prompt_task
            pipe_input.send_text("after picker\r")
            assert await asyncio.wait_for(second_read, timeout=2) == "after picker"
        finally:
            await builder.close()
