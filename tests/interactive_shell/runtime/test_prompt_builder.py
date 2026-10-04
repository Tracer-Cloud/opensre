"""Persistent prompt lifecycle regression tests."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.base import Size
from prompt_toolkit.output.vt100 import Vt100_Output

from core.domain.alerts.inbox import IncomingAlert
from surfaces.interactive_shell.runtime.core import prompt_builder as prompt_builder_module
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


def _terminal_output(stream: io.StringIO | None = None) -> Vt100_Output:
    return Vt100_Output(
        stream if stream is not None else io.StringIO(),
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )


def test_internal_picker_history_does_not_block_idle_banner_repaint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.history = [{"type": "slash", "text": "/choose", "ok": True}]
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = SimpleNamespace(output=_terminal_output())  # type: ignore[assignment]
    monkeypatch.setattr(prompt_builder_module, "render_launch_banner", lambda *_a, **_kw: None)

    assert builder._rerender_banner_if_idle() is not None


def test_idle_banner_repaint_preserves_restored_messages_without_history() -> None:
    session = Session()
    session.agent.messages = [
        ("user", "What changed?"),
        ("assistant", "The deployment rolled back."),
    ]
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = object()  # type: ignore[assignment]

    assert session.history == []
    assert builder._rerender_banner_if_idle() is None


def test_empty_shell_builds_banner_repaint_without_writing_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    stdout = io.StringIO()
    builder.pt_app = SimpleNamespace(output=_terminal_output(stdout))  # type: ignore[assignment]
    monkeypatch.setattr(
        prompt_builder_module,
        "repl_clear_screen",
        lambda: pytest.fail("banner preparation must not write through patched stdout"),
        raising=False,
    )
    monkeypatch.setattr(
        prompt_builder_module,
        "render_launch_banner",
        lambda console, **_kwargs: console.print("banner"),
        raising=False,
    )

    rendered = builder._rerender_banner_if_idle()

    assert rendered is not None
    assert "banner" in rendered
    assert stdout.getvalue() == ""


def test_pre_turn_alert_blocks_idle_banner_repaint() -> None:
    session = Session()
    session.record_incoming_alert(IncomingAlert(text="database latency"))
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = object()  # type: ignore[assignment]

    assert builder._rerender_banner_if_idle() is None


def test_rotated_session_notice_blocks_idle_banner_repaint() -> None:
    session = Session()
    session.terminal.history_generation = 1
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = object()  # type: ignore[assignment]

    assert builder._rerender_banner_if_idle() is None


def test_idle_banner_repaint_does_not_drain_active_prompt_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = SimpleNamespace(output=_terminal_output())  # type: ignore[assignment]
    drain_calls: list[bool] = []
    monkeypatch.setattr(
        prompt_builder_module,
        "drain_stale_cpr_bytes",
        lambda: drain_calls.append(True),
    )
    monkeypatch.setattr(prompt_builder_module, "render_launch_banner", lambda *_a, **_kw: None)

    assert builder._rerender_banner_if_idle() is not None
    assert drain_calls == []


@pytest.mark.asyncio
async def test_reading_from_the_live_prompt_does_not_drain_its_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The persistent prompt app reads stdin between turns. Draining under it
    # split escape sequences it was parsing, and a lone ESC then cancelled a turn.
    monkeypatch.setenv("TERM", "xterm-256color")
    drains: list[bool] = []
    monkeypatch.setattr(
        prompt_builder_module,
        "drain_stale_cpr_bytes",
        lambda: drains.append(True),
    )
    with (
        create_pipe_input() as pipe_input,
        create_app_session(input=pipe_input, output=_terminal_output()),
    ):
        session = Session()
        pt_session = build_prompt_session(session)
        pt_session.history = InMemoryHistory()
        pt_session.default_buffer.history = pt_session.history
        builder = PromptBuilder(session, ReplState(), SpinnerState(), pt_session)
        builder.setup()
        try:
            first_read = asyncio.create_task(builder.read_prompt_text())
            await _wait_until_running(builder)
            pipe_input.send_text("first\r")
            assert await asyncio.wait_for(first_read, timeout=2) == "first"
            assert drains == [True]  # once, before the prompt app started

            second_read = asyncio.create_task(builder.read_prompt_text())
            pipe_input.send_text("second\r")
            assert await asyncio.wait_for(second_read, timeout=2) == "second"
        finally:
            await builder.close()

    assert drains == [True]


@pytest.mark.asyncio
async def test_close_restores_autowrap_after_prompt_cancellation() -> None:
    builder = PromptBuilder(Session(), ReplState(), SpinnerState())
    output = MagicMock()
    builder.pt_app = SimpleNamespace(output=output)  # type: ignore[assignment]
    builder._prompt_task = asyncio.create_task(asyncio.sleep(60, result=""))

    await builder.close()

    output.enable_autowrap.assert_called_once_with()
    output.flush.assert_called_once_with()


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
