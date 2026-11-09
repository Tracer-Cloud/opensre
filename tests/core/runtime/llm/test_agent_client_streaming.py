"""Agent clients stream reply text without changing the response they return.

Streaming must be invisible to the loop: the same ``AgentLLMResponse`` as an
unstreamed call, text delivered in order, and no second attempt once text has
reached the caller, because a retry would show that text twice. A caller's
cancel closes the response at the next event and is never retried.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import pytest
from anthropic.lib.streaming import MessageStream
from anthropic.types import Message, RawMessageStreamEvent
from openai.types.chat import ChatCompletion
from pydantic import TypeAdapter

from core.llm.transports.sdk.agent_clients import (
    AnthropicAgentClient,
    BedrockAgentClient,
    OpenAIAgentClient,
)
from core.llm.types import LLMRequestCancelled, LLMStreamInterrupted, ToolCall
from tests.utils.anthropic_streams import FakeMessageStream
from tests.utils.openai_streams import FakeChunkStream, FakeResponseStream

_USER = [{"role": "user", "content": "check the pods"}]

# Text deltas, then a tool call whose input arrives as JSON fragments.
_ANTHROPIC_EVENTS: list[dict[str, Any]] = [
    {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 1,
                "cache_read_input_tokens": 4,
                "cache_creation_input_tokens": 2,
            },
        },
    },
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Let me "}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "check."}},
    {"type": "content_block_stop", "index": 0},
    {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "shell_run", "input": {}},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '{"command": '},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '"kubectl get pods"}'},
    },
    {"type": "content_block_stop", "index": 1},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use", "stop_sequence": None},
        "usage": {"output_tokens": 25},
    },
    {"type": "message_stop"},
]

# The same reply as the unstreamed Messages API returns it.
_ANTHROPIC_REPLY = Message.model_validate(
    {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [
            {"type": "text", "text": "Let me check."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "shell_run",
                "input": {"command": "kubectl get pods"},
            },
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 25,
            "cache_read_input_tokens": 4,
            "cache_creation_input_tokens": 2,
        },
    }
)


class _RawEvents:
    """The raw SSE event stream ``MessageStream`` reads; counts reads, notes close."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        adapter: TypeAdapter[Any] = TypeAdapter(RawMessageStreamEvent)
        self._events = [adapter.validate_python(event) for event in events]
        self.read = 0
        self.closed = False

    def __iter__(self) -> Iterator[Any]:
        for event in self._events:
            self.read += 1
            yield event

    def close(self) -> None:
        self.closed = True


def _sdk_stream(**_kwargs: Any) -> MessageStream[Any]:
    raw = cast(Any, _RawEvents(_ANTHROPIC_EVENTS))
    return MessageStream(raw, output_format=anthropic.NOT_GIVEN)


def _recorded_sdk_streams() -> tuple[list[_RawEvents], Callable[..., MessageStream[Any]]]:
    """A ``messages.stream`` that keeps each response it opens."""
    opened: list[_RawEvents] = []

    def stream(**_kwargs: Any) -> MessageStream[Any]:
        opened.append(_RawEvents(_ANTHROPIC_EVENTS))
        return MessageStream(cast(Any, opened[-1]), output_format=anthropic.NOT_GIVEN)

    return opened, stream


def _anthropic_client(**messages_api: Any) -> AnthropicAgentClient:
    return AnthropicAgentClient(
        model="claude-sonnet-4-6",
        client=SimpleNamespace(messages=SimpleNamespace(**messages_api)),
    )


def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.llm.transports.sdk.agent_clients.time.sleep", lambda _: None)


def test_streamed_anthropic_reply_matches_the_unstreamed_response() -> None:
    deltas: list[str] = []
    streamed = _anthropic_client(stream=_sdk_stream).invoke(_USER, on_text_delta=deltas.append)
    unstreamed_client = _anthropic_client(create=lambda **_: _ANTHROPIC_REPLY)
    unstreamed_client._streaming_enabled = False
    unstreamed = unstreamed_client.invoke(_USER)

    assert deltas == ["Let me ", "check."]
    assert streamed.tool_calls == [
        ToolCall(id="toolu_1", name="shell_run", input={"command": "kubectl get pods"})
    ]
    # Replayed verbatim as the next assistant turn, so it must serialize the same.
    assert [block.to_dict() for block in streamed.raw_content] == [
        block.to_dict() for block in unstreamed.raw_content
    ]
    assert replace(streamed, raw_content=None) == replace(unstreamed, raw_content=None)


def test_anthropic_failure_before_any_text_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_backoff(monkeypatch)
    attempts = [
        FakeMessageStream(_ANTHROPIC_REPLY, texts=[], error=ConnectionError("connection reset")),
        FakeMessageStream(_ANTHROPIC_REPLY, texts=["Let me ", "check."]),
    ]
    calls: list[dict[str, Any]] = []

    def stream(**kwargs: Any) -> FakeMessageStream:
        calls.append(kwargs)
        return attempts[len(calls) - 1]

    deltas: list[str] = []
    response = _anthropic_client(stream=stream).invoke(_USER, on_text_delta=deltas.append)

    assert len(calls) == 2
    assert deltas == ["Let me ", "check."]
    assert response.content == "Let me check."


def test_anthropic_failure_after_text_propagates_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_backoff(monkeypatch)
    calls: list[dict[str, Any]] = []

    def stream(**kwargs: Any) -> FakeMessageStream:
        calls.append(kwargs)
        return FakeMessageStream(
            _ANTHROPIC_REPLY, texts=["Let me "], error=ConnectionError("connection reset")
        )

    deltas: list[str] = []
    with pytest.raises(LLMStreamInterrupted, match="after output began: connection reset") as cut:
        _anthropic_client(stream=stream).invoke(_USER, on_text_delta=deltas.append)

    assert len(calls) == 1
    assert deltas == ["Let me "]
    # The text the caller already showed comes back, so the reply can be continued.
    assert cut.value.partial_text == "Let me "


def test_anthropic_cancel_mid_reply_closes_the_response_without_retry() -> None:
    opened, stream = _recorded_sdk_streams()
    deltas: list[str] = []

    with pytest.raises(LLMRequestCancelled):
        _anthropic_client(stream=stream).invoke(
            _USER, on_text_delta=deltas.append, should_stop=lambda: bool(deltas)
        )

    assert deltas == ["Let me "]
    assert len(opened) == 1
    assert opened[0].closed
    assert opened[0].read < len(_ANTHROPIC_EVENTS)


def test_anthropic_cancel_before_any_text_stops_at_the_next_event() -> None:
    """A reply still thinking or writing a tool call has sent no text yet; it stops too."""
    opened, stream = _recorded_sdk_streams()
    answers = iter([False, True])  # before the request goes out, then at its first event
    deltas: list[str] = []

    with pytest.raises(LLMRequestCancelled):
        _anthropic_client(stream=stream).invoke(
            _USER, on_text_delta=deltas.append, should_stop=lambda: next(answers)
        )

    # Unlike a failure before any text, a cancel is not retried.
    assert len(opened) == 1
    assert (opened[0].read, opened[0].closed) == (1, True)
    assert deltas == []


def test_bedrock_without_the_streaming_permission_answers_unstreamed() -> None:
    """An IAM policy may grant InvokeModel but not InvokeModelWithResponseStream."""
    refused = anthropic.PermissionDeniedError.__new__(anthropic.PermissionDeniedError)
    Exception.__init__(
        refused,
        "User: arn:aws:iam::123456789012:user/sre is not authorized to perform: "
        "bedrock:InvokeModelWithResponseStream on resource: arn:aws:bedrock:us-east-1::model",
    )
    streams: list[dict[str, Any]] = []
    creates: list[dict[str, Any]] = []

    def stream(**kwargs: Any) -> Any:
        streams.append(kwargs)
        raise refused

    def create(**kwargs: Any) -> Message:
        creates.append(kwargs)
        return _ANTHROPIC_REPLY

    client = BedrockAgentClient.__new__(BedrockAgentClient)
    client._model = "us.anthropic.claude-sonnet-4-6"
    client._max_tokens = 1024
    client._client = cast(
        Any, SimpleNamespace(messages=SimpleNamespace(stream=stream, create=create))
    )

    first = client.invoke(_USER, on_text_delta=lambda _: None)
    second = client.invoke(_USER, on_text_delta=lambda _: None)

    assert first.content == second.content == "Let me check."
    assert (len(streams), len(creates)) == (1, 2)


# Chat Completions: text deltas, then one tool call assembled across chunks.
_CHUNK_BASE = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "gpt-4o"}
_CHAT_CHUNKS = [
    {**_CHUNK_BASE, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]},
    {**_CHUNK_BASE, "choices": [{"index": 0, "delta": {"content": "Let me "}}]},
    {**_CHUNK_BASE, "choices": [{"index": 0, "delta": {"content": "check."}}]},
    {
        **_CHUNK_BASE,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "shell_run", "arguments": '{"command": '},
                        }
                    ]
                },
            }
        ],
    },
    {
        **_CHUNK_BASE,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"ls"}'}}]},
            }
        ],
    },
    {**_CHUNK_BASE, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    {
        **_CHUNK_BASE,
        "choices": [],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "prompt_tokens_details": {"cached_tokens": 4},
        },
    },
]


def _openai_client(*, api_key_env: str = "OPENAI_API_KEY", **sdk: Any) -> OpenAIAgentClient:
    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = SimpleNamespace(**sdk)
    client._model = "gpt-4o"
    client._max_tokens = 1024
    client._api_key_env = api_key_env
    return client


def test_streamed_openai_chat_reply_replays_like_an_unstreamed_one() -> None:
    requests: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> FakeChunkStream:
        requests.append(kwargs)
        return FakeChunkStream(_CHAT_CHUNKS)

    client = _openai_client(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    deltas: list[str] = []
    response = client.invoke(_USER, on_text_delta=deltas.append)

    assert requests[0]["stream"] is True
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert deltas == ["Let me ", "check."]
    assert response.tool_calls == [ToolCall(id="call_1", name="shell_run", input={"command": "ls"})]
    # No delta ``index`` on the replayed tool call: the unstreamed API has none.
    assert response.raw_content == {
        "role": "assistant",
        "content": "Let me check.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "shell_run", "arguments": '{"command": "ls"}'},
            }
        ],
    }
    assert (response.input_tokens, response.output_tokens, response.cache_read_tokens) == (
        10,
        5,
        4,
    )


def test_openai_cancel_mid_reply_closes_the_response_without_retry() -> None:
    opened: list[FakeChunkStream] = []

    def create(**_kwargs: Any) -> FakeChunkStream:
        opened.append(FakeChunkStream(_CHAT_CHUNKS))
        return opened[-1]

    client = _openai_client(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    deltas: list[str] = []

    with pytest.raises(LLMRequestCancelled):
        client.invoke(_USER, on_text_delta=deltas.append, should_stop=lambda: bool(deltas))

    assert deltas == ["Let me "]
    assert len(opened) == 1
    assert opened[0].closed


def test_openai_compatible_provider_answers_unstreamed() -> None:
    """Compatible providers stream tool calls their own way; they are not streamed."""
    requests: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> ChatCompletion:
        requests.append(kwargs)
        return ChatCompletion.model_validate(
            {
                "id": "c1",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
            }
        )

    client = _openai_client(
        api_key_env="DEEPSEEK_API_KEY",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    deltas: list[str] = []
    response = client.invoke(_USER, on_text_delta=deltas.append)

    assert "stream" not in requests[0]
    assert deltas == []
    assert response.content == "ok"


def test_streamed_openai_responses_reply_delivers_text_and_the_final_response() -> None:
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_text.delta", delta="All "),
        SimpleNamespace(type="response.output_text.delta", delta="pods are ready."),
        SimpleNamespace(type="response.completed"),
    ]
    final = SimpleNamespace(output=[], output_text="All pods are ready.", usage=None)
    client = _openai_client(
        responses=SimpleNamespace(stream=lambda **_: FakeResponseStream(events, final))
    )
    client._model = "gpt-5.6-sol"
    deltas: list[str] = []

    response = client.invoke(_USER, on_text_delta=deltas.append)

    assert deltas == ["All ", "pods are ready."]
    assert response.content == "All pods are ready."
    assert response.tool_calls == []
