"""Prompt-cache markers must degrade, never break a request.

Bedrock rejects ``cache_control`` with a 400 for Claude models that predate
prompt caching, and every Anthropic-shaped client sends the marker
unconditionally. Without a fallback, one env var pinning an old model
(``BEDROCK_REASONING_MODEL=anthropic.claude-3-...``) turns every LLM call into
a hard failure. The contract pinned here: a 400 that blames the markers is
retried once without them, the client stops marking for its lifetime, and any
other 400 keeps its existing error mapping.
"""

from __future__ import annotations

import types
from types import SimpleNamespace
from typing import Any

import pytest

import core.llm.transports.sdk.llm_clients as sdk_llm
from core.llm.transports.sdk.agent_clients import AnthropicAgentClient
from core.llm.transports.sdk.anthropic_cache import (
    is_cache_unsupported_error,
    strip_cache_markers,
)
from tests.utils.anthropic_streams import FakeMessageStream

_CACHE_REJECTION_MESSAGE = (
    "messages.0: Extra inputs are not permitted: cache_control is not supported for this model"
)


def _bad_request(message: str) -> Exception:
    """Real AnthropicBadRequestError without httpx response plumbing."""
    err = sdk_llm.AnthropicBadRequestError.__new__(sdk_llm.AnthropicBadRequestError)
    Exception.__init__(err, message)
    err.status_code = 400  # type: ignore[attr-defined]
    err.message = message  # type: ignore[attr-defined]
    err.body = {}  # type: ignore[attr-defined]
    err.request = None  # type: ignore[assignment]
    err.response = None  # type: ignore[assignment]
    return err


class _FlakyMessages:
    """messages.create (or .stream) that raises on the first call, then succeeds."""

    def __init__(self, first_error: Exception) -> None:
        self.calls: list[dict[str, Any]] = []
        self._first_error = first_error

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise self._first_error
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=2),
        )

    def stream(self, **kwargs: Any) -> FakeMessageStream:
        return FakeMessageStream(self.create(**kwargs))


class _InactiveGuardrailEvaluator:
    is_active = False

    def __call__(self) -> _InactiveGuardrailEvaluator:
        return self


def _has_cache_marker(kwargs: dict[str, Any]) -> bool:
    blocks: list[Any] = []
    system = kwargs.get("system")
    if isinstance(system, list):
        blocks.extend(system)
    blocks.extend(kwargs.get("tools") or [])
    for message in kwargs.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            blocks.extend(content)
    return any(isinstance(block, dict) and "cache_control" in block for block in blocks)


class TestCacheHelpers:
    def test_is_cache_unsupported_error_matches_marker_rejections(self) -> None:
        assert is_cache_unsupported_error(_bad_request(_CACHE_REJECTION_MESSAGE))
        assert is_cache_unsupported_error(
            _bad_request("This model does not support prompt caching.")
        )

    def test_is_cache_unsupported_error_ignores_other_bad_requests(self) -> None:
        assert not is_cache_unsupported_error(_bad_request("on-demand throughput isn't supported"))
        assert not is_cache_unsupported_error(_bad_request("max_tokens is too large"))

    def test_strip_removes_markers_from_system_tools_and_messages(self) -> None:
        # Arrange
        kwargs = {
            "model": "m",
            "system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}],
            "tools": [{"name": "t", "cache_control": {"type": "ephemeral"}}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "u", "cache_control": {"type": "ephemeral"}}
                    ],
                }
            ],
        }

        # Act
        stripped = strip_cache_markers(kwargs)

        # Assert
        assert not _has_cache_marker(stripped)
        # Lone text block collapses back to the plain string a non-caching
        # client would have sent (models that reject markers may also reject
        # block-form system).
        assert stripped["system"] == "s"
        assert stripped["tools"] == [{"name": "t"}]
        assert _has_cache_marker(kwargs), "input kwargs must not be mutated"

    def test_strip_keeps_user_schema_property_named_cache_control(self) -> None:
        """Only the marker positions are stripped, never tool schema bodies."""
        # Arrange
        schema = {"properties": {"cache_control": {"type": "string"}}}
        kwargs = {
            "tools": [{"name": "t", "input_schema": schema, "cache_control": {"type": "ephemeral"}}]
        }

        # Act
        stripped = strip_cache_markers(kwargs)

        # Assert
        (tool,) = stripped["tools"]
        assert "cache_control" not in tool
        assert tool["input_schema"] == schema


class TestBedrockMessagesClientFallback:
    def _client(self, messages: _FlakyMessages) -> sdk_llm.BedrockLLMClient:
        client = sdk_llm.BedrockLLMClient.__new__(sdk_llm.BedrockLLMClient)
        client._model = "anthropic.claude-3-haiku-20240307-v1:0"
        client._max_tokens = 256
        client._temperature = None
        client._anthropic_client = types.SimpleNamespace(messages=messages)  # type: ignore[assignment]
        return client

    @pytest.fixture(autouse=True)
    def _inactive_guardrails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
            _InactiveGuardrailEvaluator(),
        )

    def test_cache_rejection_retries_without_markers(self) -> None:
        # Arrange
        messages = _FlakyMessages(_bad_request(_CACHE_REJECTION_MESSAGE))
        client = self._client(messages)

        # Act
        response = client._invoke_anthropic(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        )

        # Assert
        assert response.content == "ok"
        assert len(messages.calls) == 2
        assert _has_cache_marker(messages.calls[0])
        assert not _has_cache_marker(messages.calls[1])
        assert messages.calls[1]["system"] == "sys"

    def test_fallback_is_sticky_for_the_client_lifetime(self) -> None:
        # Arrange — first invoke must carry a system prefix so markers are
        # present; without them there is nothing to strip and the 400 raises.
        messages = _FlakyMessages(_bad_request(_CACHE_REJECTION_MESSAGE))
        client = self._client(messages)
        client._invoke_anthropic(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        )

        # Act
        client._invoke_anthropic(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "again"}]
        )

        # Assert: exactly one failed marked attempt ever, no markers afterwards.
        assert len(messages.calls) == 3
        assert _has_cache_marker(messages.calls[0])
        assert not _has_cache_marker(messages.calls[1])
        assert not _has_cache_marker(messages.calls[2])
        assert messages.calls[2]["system"] == "sys"

    def test_unrelated_bad_request_keeps_existing_error_mapping(self) -> None:
        # Arrange
        messages = _FlakyMessages(_bad_request("on-demand throughput isn't supported"))
        client = self._client(messages)

        # Act / Assert
        with pytest.raises(RuntimeError, match="inference profile"):
            client._invoke_anthropic([{"role": "user", "content": "hi"}])
        assert len(messages.calls) == 1, "unrelated 400s must not be retried"


class TestAnthropicMessagesClientFallback:
    @pytest.fixture(autouse=True)
    def _inactive_guardrails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
            _InactiveGuardrailEvaluator(),
        )

    def _client(self, messages: _FlakyMessages) -> sdk_llm.LLMClient:
        client = sdk_llm.LLMClient.__new__(sdk_llm.LLMClient)
        client._model = "claude-sonnet-4-6"
        client._max_tokens = 256
        client._temperature = None
        client._api_key = "test-key"
        client._client = types.SimpleNamespace(messages=messages)  # type: ignore[assignment]
        return client

    def test_cache_rejection_retries_stripped_and_disables_markers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        messages = _FlakyMessages(_bad_request(_CACHE_REJECTION_MESSAGE))
        client = self._client(messages)
        monkeypatch.setattr(client, "_ensure_client", lambda: None)  # keep the injected fake client

        # Act
        response = client.invoke(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        )

        # Assert
        assert response.content == "ok"
        assert len(messages.calls) == 2
        assert _has_cache_marker(messages.calls[0])
        assert not _has_cache_marker(messages.calls[1])
        assert not client._cache_markers_enabled
        # And the next request is built without markers from the start.
        kwargs = client._build_request_kwargs(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        )
        assert kwargs["system"] == "sys"

    def test_unrelated_bad_request_still_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        messages = _FlakyMessages(_bad_request("max_tokens is too large"))
        client = self._client(messages)
        monkeypatch.setattr(client, "_ensure_client", lambda: None)

        # Act / Assert
        with pytest.raises(RuntimeError):
            client.invoke("hi")
        assert len(messages.calls) == 1


class TestAgentClientFallback:
    def _client(self, messages: _FlakyMessages) -> AnthropicAgentClient:
        return AnthropicAgentClient(
            model="claude-sonnet-4-6",
            client=types.SimpleNamespace(messages=messages),
        )

    def test_cache_rejection_retries_without_markers_across_all_positions(self) -> None:
        # Arrange
        messages = _FlakyMessages(_bad_request(_CACHE_REJECTION_MESSAGE))
        client = self._client(messages)
        tools = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]

        # Act
        response = client.invoke([{"role": "user", "content": "hi"}], system="sys", tools=tools)

        # Assert
        assert response.content == "ok"
        assert len(messages.calls) == 2
        assert _has_cache_marker(messages.calls[0])
        assert not _has_cache_marker(messages.calls[1])
        assert messages.calls[1]["system"] == "sys"
        assert messages.calls[1]["tools"] == tools
        assert not client._cache_markers_enabled

    def test_unrelated_bad_request_still_raises(self) -> None:
        # Arrange
        messages = _FlakyMessages(_bad_request("tools.0: input_schema is invalid"))
        client = self._client(messages)

        # Act / Assert
        with pytest.raises(RuntimeError, match="request rejected"):
            client.invoke([{"role": "user", "content": "hi"}], system="sys")
        assert len(messages.calls) == 1
