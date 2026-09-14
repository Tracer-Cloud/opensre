from __future__ import annotations

import types
from collections.abc import Iterator
from typing import Any

import pytest

from core.llm.transports.litellm.clients import LiteLLMAgentClient, LiteLLMLLMClient


class _FakeMessage:
    def __init__(self, *, content: str, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, *, exclude_none: bool = False) -> dict[str, Any]:
        _ = exclude_none
        return {"role": "assistant", "content": self.content, "tool_calls": self.tool_calls}


def _fake_response(*, content: str = "ok", tool_calls: list[Any] | None = None) -> Any:
    message = _FakeMessage(content=content, tool_calls=tool_calls)
    choice = types.SimpleNamespace(message=message, finish_reason="stop")
    usage = types.SimpleNamespace(prompt_tokens=11, completion_tokens=7)
    return types.SimpleNamespace(choices=[choice], usage=usage)


def test_litellm_agent_client_invokes_openai_compatible_endpoint() -> None:
    captured: dict[str, Any] = {}

    def completion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _fake_response(content="hello")

    client = LiteLLMAgentClient(
        litellm_model="openai/deepseek-v4-pro",
        max_tokens=123,
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        credential_resolver=lambda _env: "ds-key",
        completion_func=completion,
    )

    response = client.invoke(
        [{"role": "user", "content": "hi"}],
        system="system prompt",
        tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
    )

    assert response.content == "hello"
    assert captured["model"] == "openai/deepseek-v4-pro"
    assert captured["api_base"] == "https://api.deepseek.com"
    assert captured["api_key"] == "ds-key"
    assert captured["max_tokens"] == 123
    assert captured["tool_choice"] == "auto"
    assert captured["parallel_tool_calls"] is False
    assert captured["drop_params"] is True
    assert captured["messages"][0] == {"role": "system", "content": "system prompt"}


def test_litellm_agent_client_parses_tool_calls() -> None:
    tool_call = types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(name="lookup", arguments='{"service": "api"}'),
    )
    client = LiteLLMAgentClient(
        litellm_model="openai/deepseek-v4-pro",
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        credential_resolver=lambda _env: "ds-key",
        completion_func=lambda **_kwargs: _fake_response(content="", tool_calls=[tool_call]),
    )

    response = client.invoke([{"role": "user", "content": "hi"}])

    assert response.stop_reason == "stop"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "lookup"
    assert response.tool_calls[0].input == {"service": "api"}
    assert response.raw_content["tool_calls"] == [tool_call]


def test_litellm_agent_client_invoke_strips_internal_message_markers() -> None:
    captured: dict[str, Any] = {}

    def completion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _fake_response(content="ok")

    client = LiteLLMAgentClient(
        litellm_model="anthropic/claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        credential_resolver=lambda _env: "test-key",
        completion_func=completion,
    )

    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "seed", "name": "n", "input": {}}],
            "_opensre_seed": True,
        },
    ]
    client.invoke(messages)

    api_messages = captured["messages"]
    assert api_messages[1] == {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "seed", "name": "n", "input": {}}],
    }
    assert messages[1]["_opensre_seed"] is True


def test_litellm_agent_client_emits_global_usage_hook() -> None:
    from core.llm.shared.usage import set_usage_hook

    client = LiteLLMAgentClient(
        litellm_model="openai/deepseek-v4-pro",
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        credential_resolver=lambda _env: "ds-key",
        completion_func=lambda **_kwargs: _fake_response(content="hello"),
    )

    usage: list[tuple[str, int, int]] = []
    set_usage_hook(
        lambda model, tokens_in, tokens_out: usage.append((model, tokens_in, tokens_out))
    )
    try:
        client.invoke([{"role": "user", "content": "hi"}])
    finally:
        set_usage_hook(None)

    assert usage == [("openai/deepseek-v4-pro", 11, 7)]


def test_litellm_llm_client_emits_usage_callback() -> None:
    usage: list[tuple[str, int | None, int | None]] = []
    client = LiteLLMLLMClient(
        litellm_model="openai/groq-model",
        api_base="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        credential_resolver=lambda _env: "groq-key",
        completion_func=lambda **_kwargs: _fake_response(content="done"),
        usage_callback=lambda model, tokens_in, tokens_out: usage.append(
            (model, tokens_in, tokens_out)
        ),
    )

    response = client.invoke("hi")

    assert response.content == "done"
    assert response.input_tokens == 11
    assert response.output_tokens == 7
    assert usage == [("openai/groq-model", 11, 7)]


def test_litellm_llm_client_invoke_strips_internal_message_markers() -> None:
    captured: dict[str, Any] = {}

    def completion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _fake_response(content="ok")

    client = LiteLLMLLMClient(
        litellm_model="anthropic/claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        credential_resolver=lambda _env: "test-key",
        completion_func=completion,
    )

    messages = [{"role": "assistant", "content": "ok", "_opensre_seed": True}]
    client.invoke(messages)

    assert captured["messages"] == [{"role": "assistant", "content": "ok"}]
    assert messages[0]["_opensre_seed"] is True


class NotFoundError(Exception):
    """Simulates LiteLLM/OpenAI NotFoundError by class name."""

    pass


def test_litellm_llm_client_invoke_stream_not_found_raises_without_retry(monkeypatch) -> None:
    """NotFoundError with no model fallback must fail fast, not burn retry slots."""
    attempts: list[bool] = []
    sleeps: list[float] = []

    def completion(**_kwargs: Any) -> Any:
        attempts.append(True)
        raise NotFoundError("model not found")

    monkeypatch.setattr(
        "core.llm.shared.openai_chat_completions.time.sleep", lambda s: sleeps.append(s)
    )

    client = LiteLLMLLMClient(
        litellm_model="openai/missing-model",
        api_key_env="OPENAI_API_KEY",
        credential_resolver=lambda _env: "key",
        completion_func=completion,
    )

    with pytest.raises(RuntimeError, match="model 'openai/missing-model' was not found"):
        list(client.invoke_stream("hi"))

    assert len(attempts) == 1
    assert sleeps == []


def test_litellm_llm_client_invoke_stream_azure_not_found_mentions_deployment(
    monkeypatch,
) -> None:
    attempts: list[bool] = []

    def completion(**_kwargs: Any) -> Any:
        attempts.append(True)
        raise NotFoundError("deployment not found")

    monkeypatch.setattr("core.llm.shared.openai_chat_completions.time.sleep", lambda _s: None)

    client = LiteLLMLLMClient(
        litellm_model="azure/gpt-4.1",
        api_base="https://example.openai.azure.com",
        api_key_env="AZURE_OPENAI_API_KEY",
        credential_resolver=lambda _env: "key",
        completion_func=completion,
    )

    with pytest.raises(RuntimeError, match="Azure OpenAI deployment 'gpt-4.1' was not found"):
        list(client.invoke_stream("hi"))

    assert len(attempts) == 1


def test_litellm_llm_client_invoke_stream_retries_before_emit(monkeypatch) -> None:
    """Transient failure before any chunk is yielded should retry."""
    attempts: list[bool] = []

    def completion(**kwargs: Any) -> Any:
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        stream = kwargs.get("stream")

        def _chunks() -> Iterator[Any]:
            chunk = types.SimpleNamespace(
                choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content="ok"))]
            )
            yield chunk

        return _chunks() if stream else _fake_response(content="ok")

    monkeypatch.setattr("core.llm.shared.openai_chat_completions.time.sleep", lambda _s: None)

    client = LiteLLMLLMClient(
        litellm_model="openai/groq-model",
        api_key_env="GROQ_API_KEY",
        credential_resolver=lambda _env: "groq-key",
        completion_func=completion,
    )

    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["ok"]
    assert len(attempts) == 2
