"""Prompt redraw regression tests for background terminal output."""

from __future__ import annotations

import asyncio
import io
import re

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.base import Size
from prompt_toolkit.output.vt100 import Vt100_Output

from infrastructure.terminal.prompt_support import repl_reset_ctrl_c_gate
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.ci_fix_status import prompt_status_ansi
from surfaces.interactive_shell.ui.input_prompt import build_prompt_session
from surfaces.interactive_shell.ui.input_prompt.key_bindings import (
    build_cancel_key_bindings,
    install_session_key_bindings,
)
from surfaces.interactive_shell.ui.input_prompt.stdout import patch_prompt_stdout
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

# prompt_toolkit skips rewriting a space when the previous cell was already a
# space (``ESC[C`` / ``ESC[nC``). After Ready fills the status row, the Ctrl+C
# hint lands on those columns as cursor-forward — correct on a real TTY, but
# absent as literal `` `` in the VT100 capture.
_CURSOR_FORWARD_RE = re.compile(r"\x1b\[(\d*)C")


def _visible_prompt_text(raw: str) -> str:
    """Expand cursor-forward skips so markers match what the user sees."""

    def _expand(match: re.Match[str]) -> str:
        return " " * int(match.group(1) or "1")

    return _CURSOR_FORWARD_RE.sub(_expand, raw)


async def _wait_for_output(buffer: io.StringIO, marker: str, *, count: int = 1) -> str:
    for _ in range(200):
        rendered = buffer.getvalue()
        if _visible_prompt_text(rendered).count(marker) >= count:
            return rendered
        await asyncio.sleep(0.01)
    raise AssertionError(f"prompt output never rendered {marker!r} {count} time(s)")


@pytest.mark.asyncio
async def test_background_output_is_inserted_above_the_redrawn_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )

    with create_pipe_input() as pipe_input, create_app_session(input=pipe_input, output=output):
        session = Session()
        prompt = build_prompt_session(session, status_line=lambda: prompt_status_ansi(session))
        with patch_prompt_stdout(prompt.app, raw=True):
            prompt_task = asyncio.create_task(
                prompt.prompt_async(
                    message=render_prompt_region(session, ReplState(), SpinnerState()),
                    bottom_toolbar=lambda: "",
                    refresh_interval=0,
                )
            )
            await _wait_for_output(terminal, "Auto (")

            print("assistant response")
            rendered = await _wait_for_output(terminal, "Auto (", count=2)

            assert rendered.rfind("assistant response") < rendered.rfind("Auto (")
            assert rendered.rfind("\x1b[?2026h") < rendered.rfind("assistant response")
            assert rendered.rfind("\x1b[?2026l") > rendered.rfind("Auto (")
            pipe_input.send_bytes(b"\x04")
            with pytest.raises(EOFError):
                await asyncio.wait_for(prompt_task, timeout=2)


@pytest.mark.asyncio
async def test_ctrl_c_updates_the_live_prompt_before_second_press_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    state = ReplState()

    repl_reset_ctrl_c_gate()
    try:
        with (
            create_pipe_input() as pipe_input,
            create_app_session(
                input=pipe_input,
                output=output,
            ),
        ):
            session = Session()
            prompt = build_prompt_session(session, status_line=lambda: prompt_status_ansi(session))
            install_session_key_bindings(prompt, build_cancel_key_bindings(state))
            prompt_task = asyncio.create_task(
                prompt.prompt_async(
                    message=lambda: render_prompt_region(
                        session,
                        state,
                        SpinnerState(),
                    ),
                    bottom_toolbar=lambda: "",
                    refresh_interval=0,
                )
            )
            await _wait_for_output(terminal, "Auto (")

            pipe_input.send_bytes(b"\x03")
            await _wait_for_output(terminal, "(Press Ctrl+C again to exit)")

            assert not prompt_task.done()
            assert state.exit_requested is False

            pipe_input.send_bytes(b"\x03")
            assert await asyncio.wait_for(prompt_task, timeout=2) == ""
            assert state.exit_requested is True
    finally:
        repl_reset_ctrl_c_gate()
