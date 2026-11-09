"""A reply whose stream is cut off after text reached the user is continued, not failed."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.agent import Agent
from core.events import MessageUpdateEvent, RuntimeEvent, TurnStartEvent
from core.llm.types import (
    AgentLLMResponse,
    CancelProbe,
    LLMStreamInterrupted,
    SchemaDescribedTool,
    TextDeltaCallback,
)


class _CutOffLLM:
    """Each call streams its pieces; the calls listed in ``cut_off`` then break like a proxy limit."""

    model_id: str | None = "test-model"

    def __init__(self, replies: list[list[str]], *, cut_off: set[int]) -> None:
        self.replies = replies
        self.cut_off = cut_off
        self.requests: list[list[dict[str, Any]]] = []

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
        _ = (system, tools, should_stop)
        assert on_text_delta is not None
        call = len(self.requests)
        self.requests.append(list(messages))
        pieces = self.replies[call]
        for piece in pieces:
            on_text_delta(piece)
        if call in self.cut_off:
            raise LLMStreamInterrupted(
                "stream failed after output began", partial_text="".join(pieces)
            )
        return AgentLLMResponse(content="".join(pieces), tool_calls=[], raw_content=None)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}


def _run(llm: _CutOffLLM) -> tuple[Any, list[RuntimeEvent]]:
    events: list[RuntimeEvent] = []
    agent = Agent(
        llm=llm,
        system="sys",
        tools=[],
        max_iterations=8,
        on_runtime_event=events.append,
    )
    return agent.run([{"role": "user", "content": "explain SLOs at length"}]), events


def test_a_cut_off_reply_is_continued_into_one_answer() -> None:
    # Arrange: the first call breaks after "An SLO is a tar", the second finishes it.
    llm = _CutOffLLM([["An SLO ", "is a tar"], ["get for reliability."]], cut_off={0})

    # Act
    result, events = _run(llm)

    # Assert: one answer, no error; the model was asked to continue what it wrote.
    assert result.final_text == "An SLO is a target for reliability."
    assert result.stop_reason == "no_tools_needed"
    resumed = llm.requests[1]
    assert resumed[-2]["content"] == "An SLO is a tar"
    assert "cut off" in str(resumed[-1]["content"])
    # Surfaces keep printing into the same reply: every piece streamed once, in order,
    # and the continuing call says it continues.
    pieces = [
        event.delta
        for event in events
        if isinstance(event, MessageUpdateEvent) and event.data.get("partial")
    ]
    assert "".join(pieces) == "An SLO is a target for reliability."
    starts = [event for event in events if isinstance(event, TurnStartEvent)]
    assert [bool(start.data.get("continues_reply")) for start in starts] == [False, True]


def test_a_reply_still_cut_off_after_every_continuation_ends_as_written() -> None:
    llm = _CutOffLLM([["one "], ["two "], ["three "], ["four"]], cut_off={0, 1, 2, 3})

    result, _events = _run(llm)

    assert result.final_text == "one two three four"
    assert result.stop_reason == "reply_interrupted"
    assert len(llm.requests) == 4
