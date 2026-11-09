"""Deferred tool loading must degrade, never break a request.

A model, platform, or gateway that rejects ``defer_loading`` or ``tool_reference``
blocks must still answer. The contract pinned here: a 400 that blames deferred
loading is retried once with every revealed tool loaded up front and no
references, the client stops deferring for its lifetime, and any other 400 keeps
its existing error mapping. The cache marker never lands on a deferred tool,
which the API rejects outright.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from core.llm.transports.sdk.agent_clients import AnthropicAgentClient, BedrockAgentClient
from core.llm.types import ToolCall
from tests.utils.anthropic_streams import FakeMessageStream

_DEFERRAL_REJECTION = "tools.2.custom.defer_loading: Extra inputs are not permitted"


def _bad_request(message: str) -> Exception:
    """An SDK-shaped 400 the client's except-clause will match."""
    from anthropic import BadRequestError

    response = SimpleNamespace(status_code=400, headers={}, request=SimpleNamespace())
    return BadRequestError(message, response=response, body=None)


class _Messages:
    """messages.create and .stream that record each request and raise the queued errors first."""

    def __init__(self, *errors: Exception, before_error: Callable[[], None] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._errors = list(errors)
        self._before_error = before_error

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._errors:
            if self._before_error is not None:
                self._before_error()
            raise self._errors.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=2),
        )

    def stream(self, **kwargs: Any) -> FakeMessageStream:
        return FakeMessageStream(self.create(**kwargs))


def _tool(name: str) -> Any:
    return SimpleNamespace(
        name=name, description=f"The {name} tool.", public_input_schema={"type": "object"}
    )


def _client(cls: type[AnthropicAgentClient], messages: _Messages) -> AnthropicAgentClient:
    client = cls.__new__(cls)
    AnthropicAgentClient.__init__(
        client, model="claude-opus-4-7", client=SimpleNamespace(messages=messages)
    )
    return client


def _invoke_after_reveal(client: AnthropicAgentClient) -> Any:
    """The request after ``tool_search`` revealed ``pods``; ``logs`` stays hidden."""
    search = ToolCall(id="toolu_1", name="tool_search", input={"query": "pods"})
    found = json.dumps({"ok": True, "matches": [{"name": "pods", "description": "Pods."}]})
    messages = [
        {"role": "user", "content": "List the pods."},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": search.id, "name": search.name, "input": search.input}
            ],
        },
        client.build_tool_result_message([search], [found]),
    ]
    tools = [
        *client.tool_schemas([_tool("tool_search"), _tool("pods")]),
        *client.deferred_tool_schemas([_tool("pods"), _tool("logs")]),
    ]
    return client.invoke(messages, system="sys", tools=tools)


def _deferred(request: dict[str, Any]) -> set[str]:
    return {tool["name"] for tool in request["tools"] if tool.get("defer_loading")}


def _references(request: dict[str, Any]) -> list[str]:
    return [
        block["tool_name"]
        for message in request["messages"]
        if isinstance(message["content"], list)
        for result in message["content"]
        if result.get("type") == "tool_result" and isinstance(result.get("content"), list)
        for block in result["content"]
        if block.get("type") == "tool_reference"
    ]


def test_rejection_retries_with_the_revealed_tool_loaded_up_front() -> None:
    # Arrange
    messages = _Messages(_bad_request(_DEFERRAL_REJECTION))
    client = _client(AnthropicAgentClient, messages)

    # Act
    response = _invoke_after_reveal(client)

    # Assert
    assert response.content == "ok"
    deferring, retried = messages.calls
    assert _deferred(deferring) == {"pods", "logs"}
    assert _references(deferring) == ["pods"]
    # A deferred tool cannot carry the marker; the last tool loaded up front does.
    assert [tool["name"] for tool in deferring["tools"] if "cache_control" in tool] == [
        "tool_search"
    ]
    assert "extra_headers" not in deferring, "the first-party API needs no beta"
    # The retry has the shape requests had before deferred loading.
    assert [tool["name"] for tool in retried["tools"]] == ["tool_search", "pods"]
    assert not _deferred(retried)
    assert not _references(retried)


def test_deferral_stays_off_for_the_client_lifetime() -> None:
    # Arrange
    messages = _Messages(_bad_request(_DEFERRAL_REJECTION))
    client = _client(AnthropicAgentClient, messages)
    _invoke_after_reveal(client)

    # Act
    _invoke_after_reveal(client)

    # Assert: exactly one deferred attempt ever.
    assert len(messages.calls) == 3
    assert not _deferred(messages.calls[2])
    assert not _references(messages.calls[2])


def test_a_deferred_request_retries_after_a_concurrent_turn_turned_deferral_off() -> None:
    """Keyed on what the request carried: one client serves concurrent turns."""
    # Arrange
    client: AnthropicAgentClient

    def concurrent_rejection() -> None:
        client._deferred_tools_enabled = False

    messages = _Messages(_bad_request(_DEFERRAL_REJECTION), before_error=concurrent_rejection)
    client = _client(AnthropicAgentClient, messages)

    # Act
    response = _invoke_after_reveal(client)

    # Assert
    assert response.content == "ok"
    assert _deferred(messages.calls[0])
    assert not _deferred(messages.calls[1])


def test_unrelated_bad_request_is_not_retried() -> None:
    # Arrange
    messages = _Messages(_bad_request("max_tokens: 999999 > 128000, which is the maximum"))
    client = _client(AnthropicAgentClient, messages)

    # Act / Assert
    with pytest.raises(RuntimeError, match="HTTP 400"):
        _invoke_after_reveal(client)
    assert len(messages.calls) == 1
    assert client._deferred_tools_enabled


def test_bedrock_names_the_tool_search_beta_only_when_it_defers() -> None:
    # Arrange
    messages = _Messages()
    client = _client(BedrockAgentClient, messages)

    # Act
    _invoke_after_reveal(client)
    client.invoke(
        [{"role": "user", "content": "hi"}],
        system="sys",
        tools=client.tool_schemas([_tool("tool_search")]),
    )

    # Assert
    deferring, plain = messages.calls
    assert deferring["extra_headers"] == {"anthropic-beta": "tool-search-tool-2025-10-19"}
    assert "extra_headers" not in plain


@pytest.mark.parametrize(
    ("model", "defers"),
    [
        ("claude-opus-4-7", True),
        ("us.anthropic.claude-haiku-4-5-20251001-v1:0", True),
        ("claude-fable-5-1", True),
        ("claude-sonnet-4-20250514", False),  # the date is not a minor version
        ("claude-opus-4-1", False),
        ("claude-3-7-sonnet-20250219", False),
    ],
)
def test_only_models_that_load_tools_by_reference_declare_deferred_tools(
    model: str, defers: bool
) -> None:
    client = AnthropicAgentClient(model=model, client=SimpleNamespace(messages=_Messages()))

    assert bool(client.deferred_tool_schemas([_tool("logs")])) is defers
