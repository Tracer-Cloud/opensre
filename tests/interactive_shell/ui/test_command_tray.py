"""Command tray layout and keyboard behavior under a real prompt application."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from unittest.mock import Mock

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.application import create_app_session
from prompt_toolkit.application.current import set_app
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput

from surfaces.interactive_shell.ui.input_prompt import build_prompt_session
from surfaces.interactive_shell.ui.input_prompt.command_tray import CommandTrayControl
from surfaces.interactive_shell.ui.input_prompt.key_bindings import (
    _SHIFT_ENTER_SEQUENCE,
    build_cancel_key_bindings,
    install_session_key_bindings,
)
from surfaces.shared.terminal.prompt_layout import prompt_text_width


@pytest.fixture(autouse=True)
def _interactive_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")


class _SizedOutput(DummyOutput):
    def __init__(self, columns: int, rows: int) -> None:
        self.size = Size(rows=rows, columns=columns)

    def get_size(self) -> Size:
        return self.size


@asynccontextmanager
async def _running_prompt(
    *, columns: int = 80, rows: int = 30, hide_composer: Callable[[], bool] | None = None
) -> AsyncIterator[PromptSession[str]]:
    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=_SizedOutput(columns, rows)),
    ):
        prompt = build_prompt_session(hide_composer=hide_composer)
        prompt.history = InMemoryHistory()
        prompt.default_buffer.history = prompt.history
        rendered = asyncio.Event()

        def _on_render(_app: object) -> None:
            rendered.set()

        prompt.app.after_render += _on_render
        task = asyncio.create_task(prompt.prompt_async("Working\n> "))
        try:
            await asyncio.wait_for(rendered.wait(), timeout=5)
            with set_app(prompt.app):
                yield prompt
        finally:
            if prompt.app.is_running and not prompt.app.is_done:
                prompt.app.exit(result="")
            await asyncio.wait_for(task, timeout=5)


def _complete(prompt: PromptSession[str], text: str) -> None:
    buffer = prompt.default_buffer
    buffer.document = Document(text, len(text))
    assert prompt.completer is not None
    completions = list(
        prompt.completer.get_completions(buffer.document, CompleteEvent(completion_requested=True))
    )
    buffer.complete_state = CompletionState(buffer.document, completions)


def _press(prompt: PromptSession[str], key: Keys) -> None:
    prompt.app.key_processor.feed(KeyPress(key))
    prompt.app.key_processor.process_keys()


def _screen_lines(prompt: PromptSession[str]) -> list[str]:
    prompt.app._redraw()
    screen = prompt.app.renderer._last_screen
    assert screen is not None
    lines = [
        "".join(
            screen.data_buffer[y][x].char for x in range(prompt.app.output.get_size().columns)
        ).rstrip()
        for y in range(screen.height)
    ]
    while lines and not lines[-1]:
        lines.pop()
    return lines


@pytest.mark.asyncio
async def test_tray_is_attached_bounded_and_keeps_selected_result_visible() -> None:
    async with _running_prompt() as prompt:
        _complete(prompt, "/")
        lines = _screen_lines(prompt)
        assert lines[0] == "Working"
        assert "Commands" in lines[2]
        assert "› /integrations" in lines[3]
        assert sum("│ › /" in line or "│   /" in line for line in lines) == 6
        assert lines[-5].startswith("│") and lines[-5].endswith("│")
        assert not lines[-5].strip("│ ")
        assert "├" in lines[-3] and "Tab complete" in lines[-4]
        assert "> /" in lines[-2]
        assert all(len(line) <= 79 for line in lines)

        _press(prompt, Keys.Down)
        assert prompt.default_buffer.text == "/model"
        assert any("› /model" in line for line in _screen_lines(prompt))

        state = prompt.default_buffer.complete_state
        assert state is not None
        prompt.default_buffer.go_to_completion(len(state.completions) - 1)
        selected = state.completions[-1].display_text
        assert any(f"› {selected}" in line for line in _screen_lines(prompt))

        _press(prompt, Keys.Tab)
        assert prompt.default_buffer.text == selected
        assert prompt.default_buffer.complete_state is None
        assert not any(
            "Commands" in line or "Tab complete" in line for line in _screen_lines(prompt)
        )


@pytest.mark.asyncio
async def test_enter_submits_the_visibly_highlighted_automatic_completion() -> None:
    async with _running_prompt() as prompt:
        _complete(prompt, "/")
        state = prompt.default_buffer.complete_state
        assert state is not None and state.complete_index is None
        assert any("› /integrations" in line for line in _screen_lines(prompt))

        _press(prompt, Keys.ControlM)

        assert prompt.app.future is not None
        assert prompt.app.future.result() == "/integrations"


@pytest.mark.asyncio
async def test_modified_enter_keeps_newline_behavior_with_completions_open() -> None:
    async with _running_prompt() as prompt:
        _complete(prompt, "/")

        prompt.app.key_processor.feed(KeyPress(Keys.ControlM, _SHIFT_ENTER_SEQUENCE))
        prompt.app.key_processor.process_keys()

        assert prompt.default_buffer.text == "/\n"
        assert not prompt.app.is_done


@pytest.mark.asyncio
@pytest.mark.parametrize("columns, rows", [(40, 30), (80, 10)])
async def test_tray_adapts_to_narrow_or_short_terminal(columns: int, rows: int) -> None:
    async with _running_prompt(columns=columns, rows=rows) as prompt:
        _complete(prompt, "/")
        lines = _screen_lines(prompt)
        assert "Working" in lines[0]
        assert any("› /integrations" in line for line in lines)
        assert any("> /" in line for line in lines)
        assert len(lines) <= rows
        assert all(len(line) <= columns - 1 for line in lines)
        if columns == 40:
            assert any("Manage integrations." in line for line in lines)
        else:
            assert sum("│ › /" in line or "│   /" in line for line in lines) < 6


@pytest.mark.asyncio
async def test_resize_and_confirmation_keep_composer_and_tray_together() -> None:
    hidden = False

    def _hide_composer() -> bool:
        return hidden

    async with _running_prompt(hide_composer=_hide_composer) as prompt:
        _complete(prompt, "/effort ")
        _press(prompt, Keys.Down)
        output = prompt.app.output
        assert isinstance(output, _SizedOutput)
        output.size = Size(rows=12, columns=36)
        lines = _screen_lines(prompt)
        assert any("› medium" in line for line in lines)
        assert all(prompt_text_width(line) < 36 for line in lines)
        assert any("╰" in line and "╯" in line for line in lines)

        hidden = True
        assert _screen_lines(prompt) == ["Working"]
        hidden = False
        _press(prompt, Keys.Tab)
        assert prompt.default_buffer.text == "/effort medium"
        assert not any("Tab" in line for line in _screen_lines(prompt))


@pytest.mark.asyncio
async def test_tray_clips_terminal_cells_and_strips_metadata_controls() -> None:
    async with _running_prompt() as prompt:
        buffer = prompt.default_buffer
        buffer.complete_state = CompletionState(
            Document("/"),
            [Completion("/" + "監視" * 30, display_meta="detail\n\t\x1b\x07" + "界" * 40)],
        )
        control = CommandTrayControl(buffer)
        for width, height in [(78, 8), (34, 5), (20, 1)]:
            content = control.create_content(width, height)
            assert content.line_count <= height
            for index in range(content.line_count):
                text = "".join(fragment[1] for fragment in content.get_line(index))
                assert prompt_text_width(text) <= width
                assert not any(char in text for char in "\n\t\x1b\x07")


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_running", [False, True])
async def test_escape_closes_completions_before_clearing_or_cancelling(
    dispatch_running: bool,
) -> None:
    async with _running_prompt() as prompt:
        state = Mock()
        state.is_dispatch_running.return_value = dispatch_running
        install_session_key_bindings(prompt, build_cancel_key_bindings(state))
        _complete(prompt, "/m")
        prompt.default_buffer.go_to_completion(0)

        _press(prompt, Keys.Escape)

        assert prompt.default_buffer.complete_state is None
        assert prompt.default_buffer.text == "/m"
        state.cancel_current_dispatch.assert_not_called()

        _press(prompt, Keys.Escape)
        if dispatch_running:
            state.cancel_current_dispatch.assert_called_once()
        else:
            assert prompt.default_buffer.text == ""
