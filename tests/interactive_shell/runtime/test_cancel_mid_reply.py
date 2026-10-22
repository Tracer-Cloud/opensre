"""Esc while the model is still writing ends the turn cancelled, not as an error."""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from core.llm.types import AgentLLMResponse, CancelProbe, LLMRequestCancelled, TextDeltaCallback
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.runtime.core.state import SpinnerState
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole
from surfaces.shared.terminal.output.console_state import get_turn_spinner, set_turn_spinner
from tests.core.agent.orchestration.action_execution_test_harness import FakeActionLLM


@dataclass
class _EscMidReplyLLM(FakeActionLLM):
    """Writes the start of its reply; then the user presses Esc."""

    cancel: threading.Event = field(default_factory=threading.Event)
    preview_mid_reply: str = ""

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        should_stop: CancelProbe | None = None,
    ) -> AgentLLMResponse:
        _ = (messages, system, tools)
        assert on_text_delta is not None
        assert should_stop is not None
        on_text_delta("Checking the pods")
        spinner = get_turn_spinner()
        self.preview_mid_reply = spinner.reply_preview if spinner is not None else ""
        self.cancel.set()
        if should_stop():
            raise LLMRequestCancelled("stopped by the caller")
        raise AssertionError("the reply must stop once Esc is pressed")


def test_esc_mid_reply_cancels_the_turn_and_clears_the_live_preview() -> None:
    spinner = SpinnerState()
    spinner.start()
    cancel = threading.Event()
    screen = io.StringIO()
    console = StreamingConsole(
        spinner, cancel, output=Console(file=screen, force_terminal=False), highlight=False
    )
    llm = _EscMidReplyLLM([], cancel=cancel)
    set_turn_spinner(spinner)
    try:
        result = run_action_tool_turn(
            "check the pods", Session(), console, is_tty=False, llm_factory=lambda: llm
        )
    finally:
        set_turn_spinner(None)

    assert llm.preview_mid_reply == "Checking the pods"
    assert result.cancelled is True
    assert spinner.reply_preview == ""
    # Neither an error nor the unfinished reply reached the transcript.
    assert "error" not in screen.getvalue().lower()
    assert "Checking the pods" not in screen.getvalue()
