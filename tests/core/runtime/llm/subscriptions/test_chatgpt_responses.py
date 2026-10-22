"""Verify LiteLLM's actual HTTP/SSE path and stateless tool replay."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

import httpx
import pytest

from core.llm.subscriptions.types import SubscriptionAuth, SubscriptionLoginRequired
from core.llm.transports.litellm import responses as adapter


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


@pytest.fixture
def wire(monkeypatch):
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
        events = state["events"] or [
            {"type": "response.completed", "sequence_number": 0, "response": _response()}
        ]
        content = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
        )
        return httpx.Response(
            HTTPStatus.OK, headers={"Content-Type": "text/event-stream"}, content=content
        )

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx.Client, "_transport_for_url", lambda _self, _url: transport)
    return state


def test_real_litellm_http_preserves_tool_calls_reasoning_and_usage(wire):
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
    assert wire["requests"][0]["tools"][0]["name"] == "lookup"


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


def test_missing_subscription_never_falls_back_to_an_api_key(wire, monkeypatch):
    def missing():
        raise SubscriptionLoginRequired("Run login")

    monkeypatch.setattr(adapter, "resolve", missing)
    with pytest.raises(SubscriptionLoginRequired):
        adapter.ChatGPTResponsesAgentClient("gpt-6.1-sol").invoke(
            [{"role": "user", "content": "hi"}]
        )
    assert wire["requests"] == []


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
        assert wire["requests"][-1]["max_output_tokens"] == 512
        for role in (LLMRole.REASONING, LLMRole.CLASSIFICATION, LLMRole.TOOLCALL):
            assert isinstance(get_llm(role), adapter.ChatGPTResponsesLLMClient)
        monkeypatch.setenv("CHATGPT_MODEL", "another-model")
        assert get_llm(LLMRole.AGENT) is not agent
        assert get_llm(LLMRole.AGENT).model_id == "another-model"
    finally:
        reset_llm_clients()
