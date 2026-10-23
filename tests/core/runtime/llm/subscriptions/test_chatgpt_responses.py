"""Verify LiteLLM's actual HTTP/SSE path and stateless tool replay."""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from threading import Event, get_ident
from typing import Any

import httpx
import pytest

from core.llm.subscriptions import chatgpt_clients as adapter
from core.llm.subscriptions.types import SubscriptionAuth, SubscriptionLoginRequired
from core.llm.types import LLMRequestCancelled, LLMStreamInterrupted, ToolCall
from core.messages import MessageMapper


def _response(output: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "model": "gpt-6.1-sol",
        "output": output
        or [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "hello", "annotations": []}],
            }
        ],
        "parallel_tool_calls": True,
        "tools": [],
        "tool_choice": "auto",
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "input_tokens_details": {"cached_tokens": 5},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }


class _EventStream(httpx.SyncByteStream):
    """Lazy SSE bytes that record consumption and HTTP response closure."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.read = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for event in self.events:
            self.read += 1
            yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-use-api-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://wrong.example")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://wrong.example")
    monkeypatch.setattr(
        adapter,
        "resolve",
        lambda: SubscriptionAuth("oauth-test-token", "https://api.openai.com/v1"),
    )
    state: dict[str, Any] = {"requests": [], "events": []}

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer oauth-test-token"
        body = json.loads(request.content)
        state["requests"].append(body)
        assert body["model"] == "gpt-6.1-sol"
        assert body["store"] is False and body["stream"] is True
        assert "max_output_tokens" not in body
        assert not body.get("tools")
        events = state["events"] or [
            {"type": "response.completed", "sequence_number": 0, "response": _response()}
        ]
        stream = state.get("stream") or _EventStream(events)
        state["opened_stream"] = stream
        return httpx.Response(
            HTTPStatus.OK, headers={"Content-Type": "text/event-stream"}, stream=stream
        )

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx.Client, "_transport_for_url", lambda _self, _url: transport)
    return state


@pytest.mark.parametrize("cancel_during_auth", [False, True])
def test_cancel_before_send_never_opens_an_http_request(wire, monkeypatch, cancel_during_auth):
    stopped = False

    def resolve():
        nonlocal stopped
        stopped = True
        return SubscriptionAuth("oauth-test-token", "https://api.openai.com/v1")

    if cancel_during_auth:
        monkeypatch.setattr(adapter, "resolve", resolve)
    else:
        stopped = True
    with pytest.raises(LLMRequestCancelled):
        adapter.ChatGPTResponsesAgentClient("gpt-6.1-sol").invoke(
            [{"role": "user", "content": "hello"}], should_stop=lambda: stopped
        )
    assert wire["requests"] == []


@pytest.mark.parametrize("text_delta", [False, True])
def test_cancel_closes_http_stream_and_releases_worker_before_the_next_event(wire, text_delta):
    cancelled = Event()
    reached_tail = Event()
    release_tail = Event()
    first = (
        {
            "type": "response.output_text.delta",
            "delta": "partial",
            "sequence_number": 0,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
        }
        if text_delta
        else {"type": "response.created", "sequence_number": 0, "response": _response()}
    )

    class HeldStream(_EventStream):
        def __iter__(self) -> Iterator[bytes]:
            for chunk in super().__iter__():
                if not text_delta:
                    cancelled.set()
                yield chunk
            reached_tail.set()
            assert release_tail.wait(5), "cancelled worker read beyond its first event"

    stream = HeldStream([first])
    wire["stream"] = stream
    deltas = []

    def on_delta(text):
        deltas.append(text)
        cancelled.set()

    client = adapter.ChatGPTResponsesAgentClient("gpt-6.1-sol")
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(
            client.invoke,
            [{"role": "user", "content": "hello"}],
            should_stop=cancelled.is_set,
            on_text_delta=on_delta,
        )
        try:
            with pytest.raises(LLMRequestCancelled):
                pending.result(timeout=3)
            assert stream.closed and not reached_tail.is_set()
            assert deltas == (["partial"] if text_delta else [])
            wire.pop("stream")
            next_turn = worker.submit(client.invoke, [{"role": "user", "content": "next"}])
            assert next_turn.result(timeout=3).content == "hello"
            assert len(wire["requests"]) == 2  # one cancelled request, one next turn; no retry
        finally:
            release_tail.set()


def test_text_deltas_arrive_live_on_calling_thread_with_unchanged_replay(wire):
    output = [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "replay"},
        *_response()["output"],
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"service":"api"}',
            "status": "completed",
        },
    ]
    completed = _response(output)
    completed["output"] = []
    events = (
        [
            {
                "type": "response.output_text.delta",
                "delta": delta,
                "sequence_number": index,
                "item_id": "msg_1",
                "output_index": 1,
                "content_index": 0,
            }
            for index, delta in enumerate(["hel", "lo"])
        ]
        + [
            {
                "type": "response.output_item.done",
                "sequence_number": index + 2,
                "output_index": index,
                "item": item,
            }
            for index, item in enumerate(output)
        ]
        + [{"type": "response.completed", "sequence_number": 5, "response": completed}]
    )
    stream = _EventStream(events)
    wire["stream"] = stream
    deltas = []
    caller = get_ident()

    def on_delta(text):
        assert get_ident() == caller
        assert stream.read == len(deltas) + 1
        assert not stream.closed
        deltas.append(text)

    usage = []
    client = adapter.ChatGPTResponsesAgentClient(
        "gpt-6.1-sol", usage_callback=lambda *args: usage.append(args)
    )
    streamed = client.invoke([{"role": "user", "content": "hello"}], on_text_delta=on_delta)
    wire["stream"] = _EventStream(events)
    buffered = client.invoke([{"role": "user", "content": "hello"}])
    assert deltas == ["hel", "lo"] and stream.closed
    assert streamed == buffered
    assert streamed.content == "hello"
    assert streamed.raw_content["_openai_response_output"] == output
    assert streamed.tool_calls[0].input == {"service": "api"}
    assert usage == [("gpt-6.1-sol", 11, 7)] * 2


def test_real_litellm_http_preserves_tool_calls_reasoning_and_usage(wire, recwarn):
    output = [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "encrypted-replay"},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"service":"api"}',
            "status": "completed",
        },
    ]
    wire["events"] = [
        {"type": "response.completed", "sequence_number": 0, "response": _response(output)}
    ]
    client = adapter.ChatGPTResponsesAgentClient("gpt-6.1-sol")
    result = client.invoke(
        [{"role": "user", "content": "check api"}],
        system="Be precise",
        on_text_delta=lambda _text: None,
        should_stop=lambda: False,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Look up service",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].input == {"service": "api"}
    assert (
        result.input_tokens,
        result.output_tokens,
        result.cache_read_tokens,
        result.reasoning_tokens,
    ) == (11, 7, 5, 2)
    assert result.stop_reason == "tool_use"
    client.invoke(
        [result.raw_content, {"role": "tool", "tool_call_id": "call_1", "content": "healthy"}]
    )
    replay = wire["requests"][-1]["input"]
    assert replay[0]["encrypted_content"] == "encrypted-replay"
    assert replay[1]["call_id"] == "call_1"
    assert replay[2] == {"type": "function_call_output", "call_id": "call_1", "output": "healthy"}
    tool_item = wire["requests"][0]["input"][0]
    assert tool_item["type"] == "additional_tools"
    assert tool_item["role"] == "developer"
    assert tool_item["tools"][0]["name"] == "lookup"
    assert not any("ResponseAPIUsage" in str(warning.message) for warning in recwarn)


@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete", "interrupted"])
def test_partial_text_is_never_success_without_completed_event(wire, terminal):
    wire["events"] = [
        {
            "type": "response.output_text.delta",
            "delta": "partial",
            "sequence_number": 0,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
        }
    ]
    if terminal != "interrupted":
        failed = _response()
        failed["status"] = "failed" if terminal == "response.failed" else "incomplete"
        failed["error"] = {
            "code": "subscription_sharing_usage_limit_exceeded",
            "message": "Plan limit",
        }
        wire["events"].append({"type": terminal, "sequence_number": 1, "response": failed})
    client = adapter.ChatGPTResponsesLLMClient("gpt-6.1-sol")
    stream = client.invoke_stream("hello")
    assert next(stream) == "partial"
    with pytest.raises(RuntimeError):
        list(stream)


@pytest.mark.parametrize("deliver_text", [False, True])
@pytest.mark.parametrize(
    "terminal", ["response.failed", "response.incomplete", "eof", "disconnect"]
)
def test_agent_stream_failure_retains_only_delivered_text(wire, terminal, deliver_text):
    events = [
        {
            "type": "response.output_text.delta",
            "delta": text,
            "sequence_number": index,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
        }
        for index, text in enumerate(["part", "ial"])
    ]
    if terminal.startswith("response."):
        failed = _response()
        failed["status"] = terminal.removeprefix("response.")
        failed["error"] = {"code": "server_error", "message": "failed"}
        events.append({"type": terminal, "sequence_number": 2, "response": failed})

    class FailingStream(_EventStream):
        def __iter__(self) -> Iterator[bytes]:
            yield from super().__iter__()
            if terminal == "disconnect":
                raise httpx.ReadError("connection reset")

    stream = FailingStream(events)
    wire["stream"] = stream
    deltas = []
    usage = []
    client = adapter.ChatGPTResponsesAgentClient(
        "gpt-6.1-sol", usage_callback=lambda *args: usage.append(args)
    )
    expected = LLMStreamInterrupted if deliver_text else (RuntimeError, httpx.ReadError)
    with pytest.raises(expected) as error:
        client.invoke(
            [{"role": "user", "content": "hello"}],
            on_text_delta=deltas.append if deliver_text else None,
        )
    if deliver_text:
        assert error.value.partial_text == "partial"
    assert deltas == (["part", "ial"] if deliver_text else [])
    assert stream.closed and len(wire["requests"]) == 1
    assert usage == []


def test_missing_subscription_never_falls_back_to_an_api_key(wire, monkeypatch):
    def missing():
        raise SubscriptionLoginRequired("Run login")

    monkeypatch.setattr(adapter, "resolve", missing)
    with pytest.raises(SubscriptionLoginRequired):
        adapter.ChatGPTResponsesAgentClient("gpt-6.1-sol").invoke(
            [{"role": "user", "content": "hi"}]
        )
    assert wire["requests"] == []


def test_multiple_tool_results_round_trip_through_message_mapper(wire):
    client = adapter.ChatGPTResponsesAgentClient("gpt-6.1-sol")
    mapper = MessageMapper(client)
    calls = [
        ToolCall(id="call_1", name="lookup", input={"service": "api"}),
        ToolCall(id="call_2", name="lookup", input={"service": "worker"}),
    ]
    assistant = mapper.to_synthetic_assistant_provider_message(calls)
    results = mapper.to_tool_result_runtime_message(calls, ["healthy", "degraded"])
    assert len(results.provider_payloads) == 2
    client.invoke([assistant, *mapper.to_provider_messages([results])])
    assert wire["requests"][-1]["input"][-2:] == [
        {"type": "function_call_output", "call_id": "call_1", "output": json.dumps("healthy")},
        {"type": "function_call_output", "call_id": "call_2", "output": json.dumps("degraded")},
    ]


def test_reasoning_usage_is_counted_once(wire):
    calls = []

    def usage(*args):
        calls.append(args)

    client = adapter.ChatGPTResponsesLLMClient("gpt-6.1-sol", usage_callback=usage)
    assert client.invoke("hello").content == "hello"
    assert calls == [("gpt-6.1-sol", 11, 7)]
    calls.clear()
    assert list(client.invoke_stream("hello")) == []
    assert calls == [("gpt-6.1-sol", 11, 7)]


@pytest.mark.parametrize("tool_call", [False, True])
def test_compact_completion_retains_completed_output_items(wire, tool_call):
    output = _response()["output"]
    if tool_call:
        output = [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": '{"service":"api"}',
                "status": "completed",
            }
        ]
    completed = _response()
    completed["output"] = []
    wire["events"] = [
        {
            "type": "response.output_item.done",
            "sequence_number": index,
            "output_index": index,
            "item": item,
        }
        for index, item in enumerate(output)
    ] + [{"type": "response.completed", "sequence_number": len(output), "response": completed}]
    result = adapter.ChatGPTResponsesAgentClient("gpt-6.1-sol").invoke(
        [{"role": "user", "content": "hello"}]
    )
    assert result.raw_content["_openai_response_output"] == output
    if tool_call:
        assert result.tool_calls[0].name == "lookup"
        assert result.tool_calls[0].input == {"service": "api"}
    else:
        assert result.content == "hello"


def test_factory_routes_every_role_to_subscription_and_refreshes_model_cache(wire, monkeypatch):
    from core.llm.factory import LLMRole, get_llm, reset_llm_clients, resolve_llm_route

    monkeypatch.setenv("LLM_PROVIDER", "chatgpt")
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "local")
    monkeypatch.setenv("OPENSRE_LLM_TRANSPORT", "sdk")
    monkeypatch.setenv("CHATGPT_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("LLM_MAX_TOKENS", "512")
    reset_llm_clients()
    try:
        route = resolve_llm_route()
        assert route.provider == "chatgpt" and route.use_litellm
        assert route.cli_provider_registration is None
        agent = get_llm(LLMRole.AGENT)
        assert isinstance(agent, adapter.ChatGPTResponsesAgentClient)
        agent.invoke([{"role": "user", "content": "hello"}])
        assert "max_output_tokens" not in wire["requests"][-1]
        for role in (LLMRole.REASONING, LLMRole.CLASSIFICATION, LLMRole.TOOLCALL):
            assert isinstance(get_llm(role), adapter.ChatGPTResponsesLLMClient)
        monkeypatch.setenv("CHATGPT_MODEL", "another-model")
        assert get_llm(LLMRole.AGENT) is not agent
        assert get_llm(LLMRole.AGENT).model_id == "another-model"
    finally:
        reset_llm_clients()
