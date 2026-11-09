"""Fake OpenAI streamed responses for agent-client tests."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import Any

from openai.types.chat import ChatCompletion, ChatCompletionChunk


class FakeChunkStream:
    """``chat.completions.create(stream=True)``: a closable iterator of chunks."""

    def __init__(self, chunks: Sequence[dict[str, Any]]) -> None:
        self._chunks = [ChatCompletionChunk.model_validate(chunk) for chunk in chunks]
        self.closed = False

    def __enter__(self) -> FakeChunkStream:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.closed = True

    def __iter__(self) -> Iterator[ChatCompletionChunk]:
        return iter(self._chunks)


class FakeResponseStream:
    """``responses.stream(...)``: typed events, then the completed response."""

    def __init__(self, events: Sequence[Any], final: Any) -> None:
        self._events = list(events)
        self._final = final

    def __enter__(self) -> FakeResponseStream:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def __iter__(self) -> Iterator[Any]:
        return iter(self._events)

    def get_final_response(self) -> Any:
        return self._final


def completion_chunks(completion: ChatCompletion) -> list[dict[str, Any]]:
    """The chunks a streamed request returns for ``completion``: its delta, the finish, the usage."""
    base = {
        "id": completion.id,
        "object": "chat.completion.chunk",
        "created": completion.created,
        "model": completion.model,
    }
    choice = completion.choices[0]
    delta: dict[str, Any] = {"role": "assistant", "content": choice.message.content}
    if choice.message.tool_calls:
        delta["tool_calls"] = [
            {"index": index, **call.model_dump(exclude_none=True)}
            for index, call in enumerate(choice.message.tool_calls)
        ]
    chunks = [
        {**base, "choices": [{"index": 0, "delta": delta}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice.finish_reason}]},
    ]
    if completion.usage is not None:
        chunks.append({**base, "choices": [], "usage": completion.usage.model_dump()})
    return chunks


def response_text_events(response: Any) -> list[SimpleNamespace]:
    """The text-delta events a streamed Responses request emits for ``response``'s output text."""
    return [
        SimpleNamespace(type="response.output_text.delta", delta=part.text)
        for item in response.output
        for part in getattr(item, "content", None) or ()
        if getattr(part, "type", None) == "output_text"
    ]
