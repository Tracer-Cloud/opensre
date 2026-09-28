"""Persistent prompt lifecycle regression tests."""

from __future__ import annotations

import asyncio
import io
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.base import Size
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.text import Text

from core.domain.alerts.inbox import IncomingAlert
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


def _idle_prompt_app(*, rows: int = 30, columns: int = 80) -> SimpleNamespace:
    container = SimpleNamespace(
        preferred_height=lambda _columns, _rows: SimpleNamespace(preferred=4)
    )
    writes: list[str] = []
    erase_calls: list[bool] = []
    cursor_positions: list[tuple[int, int]] = []
    return SimpleNamespace(
        output=SimpleNamespace(
            get_size=lambda: Size(rows=rows, columns=columns),
            erase_screen=lambda: erase_calls.append(True),
            cursor_goto=lambda row, column: cursor_positions.append((row, column)),
            write_raw=writes.append,
            flush=lambda: None,
            writes=writes,
            erase_calls=erase_calls,
            cursor_positions=cursor_positions,
        ),
        layout=SimpleNamespace(container=container),
    )


def test_resize_after_resume_preserves_rendered_transcript() -> None:
    session = Session()
    session.history = [{"type": "slash", "text": "/resume target", "ok": True}]
    session.agent.messages = [{"role": "user", "content": "restored prompt"}]
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = object()  # type: ignore[assignment]
    assert session.terminal.submitted_turn_count == 0
    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is False


def test_resize_after_pre_turn_alert_preserves_rendered_transcript() -> None:
    session = Session()
    session.record_incoming_alert(IncomingAlert(text="database latency is high"))
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = object()  # type: ignore[assignment]
    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is False


def test_resize_after_internal_picker_history_rerenders_launch_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.history = [{"type": "slash", "text": "/choose", "ok": True}]
    session.terminal.remember_idle_output("Selection cancelled — type a reply instead.")
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    app = _idle_prompt_app()
    builder.pt_app = app  # type: ignore[assignment]
    banner_calls: list[bool] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.build_launch_banner",
        lambda *_args, **_kwargs: banner_calls.append(True) or Text("banner"),
    )
    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is True
    assert banner_calls == [True]
    assert app.output.erase_calls == [True]
    assert app.output.cursor_positions == [(0, 0)]
    assert "banner\r\nSelection cancelled — type a reply instead." in "".join(app.output.writes)


def test_resize_banner_bypasses_prompt_toolkit_stdout_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SIGWINCH transaction must own the clear and replacement writes."""
    session = Session()
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    app = _idle_prompt_app()
    builder.pt_app = app  # type: ignore[assignment]
    process_stdout = io.StringIO()
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.build_launch_banner",
        lambda *_args, **_kwargs: Text("banner"),
    )

    with redirect_stdout(process_stdout):
        rerendered = builder._rerender_banner_if_idle()

    assert rerendered is True
    assert process_stdout.getvalue() == ""
    assert "banner" in "".join(app.output.writes)


def test_resize_banner_strips_ansi_for_native_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native Win32 output writes escape bytes literally instead of styling them."""
    session = Session()
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    app = _idle_prompt_app()
    builder.pt_app = app  # type: ignore[assignment]
    banner = Text("banner", style="bold red")
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.build_launch_banner",
        lambda *_args, **_kwargs: banner,
    )

    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is True
    emitted = "".join(app.output.writes)
    assert "banner" in emitted
    assert "\x1b" not in emitted


def test_resize_banner_does_not_drain_active_prompt_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resize repaint must leave unread keystrokes for prompt-toolkit."""
    session = Session()
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    builder.pt_app = _idle_prompt_app()  # type: ignore[assignment]
    drain = MagicMock()
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.drain_stale_cpr_bytes",
        drain,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.build_launch_banner",
        lambda *_args, **_kwargs: Text("banner"),
    )

    assert builder._rerender_banner_if_idle() is True
    drain.assert_not_called()


def test_resize_does_not_duplicate_banner_when_idle_ui_exceeds_viewport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    app = _idle_prompt_app(rows=5)
    builder.pt_app = app  # type: ignore[assignment]
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.build_launch_banner",
        lambda *_args, **_kwargs: Text("banner\nrows"),
    )

    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is False
    assert app.output.erase_calls == []


def test_resize_uses_compact_banner_when_full_banner_exceeds_viewport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    app = _idle_prompt_app(rows=7)
    builder.pt_app = app  # type: ignore[assignment]
    banner_densities: list[str] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.drain_stale_cpr_bytes",
        lambda: None,
    )

    def _build_banner(*_args: object, density: str = "full", **_kwargs: object) -> Text:
        banner_densities.append(density)
        return Text("full\nbanner\nrows\nthat\ndo\nnot\nfit" if density == "full" else "compact")

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.build_launch_banner",
        _build_banner,
    )

    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is True
    assert banner_densities == ["full", "compact"]
    assert app.output.erase_calls == [True]


def test_resize_uses_minimal_banner_when_compact_banner_exceeds_viewport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    app = _idle_prompt_app(rows=6)
    builder.pt_app = app  # type: ignore[assignment]
    banner_densities: list[str] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.drain_stale_cpr_bytes",
        lambda: None,
    )

    def _build_banner(*_args: object, density: str = "full", **_kwargs: object) -> Text:
        banner_densities.append(density)
        return Text("banner\nrows" if density != "minimal" else "OpenSRE")

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.build_launch_banner",
        _build_banner,
    )

    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is True
    assert banner_densities == ["full", "compact", "minimal"]
    assert app.output.erase_calls == [True]


def test_resize_measures_idle_replay_at_physical_terminal_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.terminal.remember_idle_output("x" * 65)
    builder = PromptBuilder(session, ReplState(), SpinnerState())
    app = _idle_prompt_app(rows=9, columns=30)
    builder.pt_app = app  # type: ignore[assignment]
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.prompt_builder.build_launch_banner",
        lambda *_args, **_kwargs: Text("banner\nrows"),
    )

    rerendered = builder._rerender_banner_if_idle()

    assert rerendered is False
    assert app.output.erase_calls == []


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
