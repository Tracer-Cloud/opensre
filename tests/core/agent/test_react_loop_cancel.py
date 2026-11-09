"""ReAct loop cooperatively stops when the host console requests cancel."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from core.agent import Agent
from core.events import MessageStartEvent, MessageUpdateEvent, RuntimeEvent
from core.llm.types import (
    AgentLLMResponse,
    CancelProbe,
    LLMRequestCancelled,
    SchemaDescribedTool,
    TextDeltaCallback,
)
from core.messages import AssistantRuntimeMessage


@dataclass
class _CancelConsole:
    cancel_requested: bool = True


class _EventConsole:
    """The host console: cancel follows a ``threading.Event``, like Esc or ``/stop``."""

    def __init__(self, cancel: threading.Event) -> None:
        self._cancel = cancel

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()


def _tool_resources(console: object) -> dict[str, Any]:
    return {"action_tool_context": type("Ctx", (), {"console": console})()}


class _BoomLLM:
    """Must not be called once cancel is already requested."""

    model_id = "test-model"

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        raise AssertionError("ReAct loop must not call the LLM after cancel_requested")

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[object], _results: list[object]
    ) -> dict[str, object]:
        return {"role": "tool", "content": "[]"}


def test_react_loop_stops_before_llm_when_cancel_requested() -> None:
    agent = Agent(
        llm=_BoomLLM(),
        system="sys",
        tools=[],
        resolved_integrations={},
        tool_resources={"action_tool_context": type("Ctx", (), {"console": _CancelConsole()})()},
        max_iterations=3,
    )
    result = agent.run([{"role": "user", "content": "hello"}])
    assert result.cancelled is True
    assert result.terminated_by_tool is False
    assert result.hit_iteration_cap is False
    assert result.llm_iterations_used == 0
    assert result.final_text == ""


def test_react_loop_stops_after_iteration_when_cancel_flips() -> None:
    """Cancel between iterations must not start another LLM call."""
    import threading

    from core.llm.types import ToolCall

    cancel = threading.Event()

    class _FlipConsole:
        @property
        def cancel_requested(self) -> bool:
            return cancel.is_set()

    class _NoopTool:
        name = "noop"
        description = "noop"
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

        def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    class _CountingLLM(_BoomLLM):
        def __init__(self) -> None:
            self.calls = 0

        def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
            return [
                {
                    "name": getattr(t, "name", "noop"),
                    "description": "",
                    "parameters": getattr(t, "parameters", {}),
                }
                for t in tools
            ]

        def invoke(
            self,
            _messages: list[dict[str, Any]],
            *,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AgentLLMResponse:
            _ = (system, tools)
            self.calls += 1
            if self.calls == 1:
                # After tools run, cancel is set so iteration 2 must not call us.
                cancel.set()
                return AgentLLMResponse(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="noop", input={})],
                    raw_content=None,
                )
            raise AssertionError("second LLM iteration must not run after cancel")

    llm = _CountingLLM()
    agent = Agent(
        llm=llm,
        system="sys",
        tools=[_NoopTool()],
        resolved_integrations={},
        tool_resources={"action_tool_context": type("Ctx", (), {"console": _FlipConsole()})()},
        max_iterations=5,
    )
    result = agent.run([{"role": "user", "content": "hello"}])
    assert llm.calls == 1
    assert result.cancelled is True
    assert result.terminated_by_tool is False


class _StreamingLLM:
    """Streams its reply piece by piece, asking ``should_stop`` at each, like an SDK client."""

    model_id: str | None = "test-model"

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces
        self.calls = 0

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        _ = tools
        return []

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
        self.calls += 1
        for piece in self.pieces:
            if should_stop():
                raise LLMRequestCancelled("stopped by the caller")
            on_text_delta(piece)
        return AgentLLMResponse(content="".join(self.pieces), tool_calls=[], raw_content=None)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}


def test_cancel_mid_reply_ends_the_run_cancelled_without_recording_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ask the host at every event, so the cancel lands on the next piece.
    monkeypatch.setattr("core.agent.react_loop._STREAM_CANCEL_POLL_SECONDS", 0.0)
    cancel = threading.Event()
    events: list[RuntimeEvent] = []

    def on_runtime_event(event: RuntimeEvent) -> None:
        events.append(event)
        if isinstance(event, MessageUpdateEvent):
            cancel.set()  # Esc while the first piece is on screen

    llm = _StreamingLLM(["Checking ", "the pods ", "now."])
    result = Agent(
        llm=llm,
        system="sys",
        tools=[],
        resolved_integrations={},
        tool_resources=_tool_resources(_EventConsole(cancel)),
        max_iterations=3,
        on_runtime_event=on_runtime_event,
    ).run([{"role": "user", "content": "hello"}])

    assert llm.calls == 1
    assert (result.cancelled, result.stop_reason, result.final_text) == (True, "cancelled", "")
    assert result.hit_iteration_cap is False
    assert not any(isinstance(message, AssistantRuntimeMessage) for message in result.messages)
    assert [event.delta for event in events if isinstance(event, MessageUpdateEvent)] == [
        "Checking "
    ]
    assert not any(isinstance(event, MessageStartEvent) for event in events)


class _CountingConsole:
    def __init__(self) -> None:
        self.checks = 0

    @property
    def cancel_requested(self) -> bool:
        self.checks += 1
        return False


def _host_cancel_checks(pieces: int) -> int:
    console = _CountingConsole()
    Agent(
        llm=_StreamingLLM(["piece "] * pieces),
        system="sys",
        tools=[],
        resolved_integrations={},
        tool_resources=_tool_resources(console),
        max_iterations=3,
    ).run([{"role": "user", "content": "hello"}])
    return console.checks


def test_a_long_streamed_reply_does_not_check_the_host_per_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host's cancel check may read a store, as a scheduled run's does."""
    # Every event lands inside one poll interval.
    monkeypatch.setattr("core.agent.react_loop.time.monotonic", lambda: 100.0)

    assert _host_cancel_checks(50) == _host_cancel_checks(1)
