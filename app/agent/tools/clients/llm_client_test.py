from __future__ import annotations

import httpx
import pytest
from anthropic import AuthenticationError, BadRequestError, InternalServerError

from app.agent.tools.clients import llm_client


def test_get_llm_uses_openai_model_from_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")
    monkeypatch.setattr(llm_client, "_llm", None)

    client = llm_client.get_llm()

    assert isinstance(client, llm_client.OpenAILLMClient)
    assert client._model == "gpt-5-mini"
    monkeypatch.setattr(llm_client, "_llm", None)


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeTextBlock(text)]


class _FakeMessagesAPI:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    def create(self, **_kwargs):
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _status_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, request=_status_request())


def test_llm_client_retries_retryable_anthropic_errors_then_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    sleep_calls: list[float] = []
    monkeypatch.setattr(llm_client.time, "sleep", sleep_calls.append)

    client = llm_client.LLMClient(model="claude-test")
    messages_api = _FakeMessagesAPI(
        [
            InternalServerError("Overloaded", response=_status_response(529), body=None),
            _FakeResponse("all good"),
        ]
    )
    client._client = type("FakeAnthropicClient", (), {"messages": messages_api})()

    result = client.invoke("test prompt")

    assert result.content == "all good"
    assert messages_api.calls == 2
    assert sleep_calls == [2.0]


def test_llm_client_does_not_retry_bad_request_errors(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    sleep_calls: list[float] = []
    monkeypatch.setattr(llm_client.time, "sleep", sleep_calls.append)

    client = llm_client.LLMClient(model="claude-test")
    messages_api = _FakeMessagesAPI(
        [
            BadRequestError("bad request", response=_status_response(400), body=None),
        ]
    )
    client._client = type("FakeAnthropicClient", (), {"messages": messages_api})()

    with pytest.raises(BadRequestError):
        client.invoke("test prompt")

    assert messages_api.calls == 1
    assert sleep_calls == []


def test_llm_client_does_not_retry_authentication_errors(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    sleep_calls: list[float] = []
    monkeypatch.setattr(llm_client.time, "sleep", sleep_calls.append)

    client = llm_client.LLMClient(model="claude-test")
    messages_api = _FakeMessagesAPI(
        [
            AuthenticationError("unauthorized", response=_status_response(401), body=None),
        ]
    )
    client._client = type("FakeAnthropicClient", (), {"messages": messages_api})()

    with pytest.raises(RuntimeError, match="Anthropic authentication failed"):
        client.invoke("test prompt")

    assert messages_api.calls == 1
    assert sleep_calls == []
