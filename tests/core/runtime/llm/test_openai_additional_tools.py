"""Loading revealed tools from the conversation must degrade, never break a request.

On the Responses API a tool that ``tool_search`` reveals is loaded by an
``additional_tools`` input item placed after the search result, so ``tools`` —
which opens the prefix OpenAI caches — reads the same before and after a
reveal. The contract pinned here: only a tool the result reports as newly
visible moves, a result that no longer parses leaves its tool in ``tools``, a
400 that names the item is retried once with every tool in ``tools``, the
client then stops for its lifetime, and any other 400 keeps its error mapping.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from openai.types.responses import Response

from core.context_budget import EVICTED_TOOL_RESULT_TEXT
from core.llm.transports.sdk.agent_clients import OpenAIAgentClient
from core.llm.transports.sdk.openai_additional_tools import additional_tools_request
from core.llm.types import ToolCall

_ITEM_REJECTION = "Invalid value: 'additional_tools'. Supported values are: 'message'."


def _bad_request(message: str) -> Exception:
    """An SDK-shaped 400 the client's except-clause will match."""
    from openai import BadRequestError

    response = SimpleNamespace(status_code=400, headers={}, request=SimpleNamespace())
    return BadRequestError(message, response=response, body=None)


class _Responses:
    """responses.create that records each request and raises the queued errors first."""

    def __init__(self, *errors: Exception, before_error: Callable[[], None] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._errors = list(errors)
        self._before_error = before_error

    def create(self, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        if self._errors:
            if self._before_error is not None:
                self._before_error()
            raise self._errors.pop(0)
        return Response.model_validate(
            {
                "id": "resp_1",
                "object": "response",
                "created_at": 0,
                "model": kwargs["model"],
                "status": "completed",
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
                "output": [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }
        )


def _tool(name: str) -> Any:
    return SimpleNamespace(
        name=name,
        description=f"The {name} tool.",
        public_input_schema={"type": "object", "properties": {}},
    )


@pytest.fixture
def responses(monkeypatch: pytest.MonkeyPatch) -> _Responses:
    monkeypatch.setattr("core.llm.hosted_credits.admit_hosted_credits", lambda: None)
    return _Responses()


def _client(responses: _Responses) -> OpenAIAgentClient:
    client = OpenAIAgentClient(model="gpt-6.2-sol", credential_resolver=lambda _env: "test-key")
    client._client = SimpleNamespace(responses=responses)
    return client


def _search_result(*matches: dict[str, Any]) -> str:
    return json.dumps({"ok": True, "matches": list(matches)})


def _invoke_after_reveal(client: OpenAIAgentClient, result: str | None = None) -> Any:
    """The request after ``tool_search`` made ``pods`` visible beside the plan write it rode with."""
    search = ToolCall(id="call_1", name="tool_search", input={"query": "pods"})
    plan = ToolCall(id="call_2", name="update_plan", input={})
    found = result or _search_result({"name": "pods", "newly_visible": True})
    messages = [
        {"role": "user", "content": "List the pods."},
        client.build_assistant_message("", [search, plan]),
        *client.build_tool_result_messages([search, plan], [found, "{}"]),
    ]
    tools = client.tool_schemas([_tool("tool_search"), _tool("update_plan"), _tool("pods")])
    return client.invoke(messages, system="sys", tools=tools)


def _tool_names(request: dict[str, Any]) -> list[str]:
    return [tool["name"] for tool in request["tools"]]


def _loaded(request: dict[str, Any]) -> list[tuple[int, list[str]]]:
    """Each ``additional_tools`` item of a request: its position and the tools it loads."""
    return [
        (index, [tool["name"] for tool in item["tools"]])
        for index, item in enumerate(request["input"])
        if item.get("type") == "additional_tools"
    ]


def test_a_revealed_tool_loads_after_the_results_of_the_response_that_searched(
    responses: _Responses,
) -> None:
    # Act
    response = _invoke_after_reveal(_client(responses))

    # Assert
    assert response.content == "ok"
    (request,) = responses.calls
    assert _tool_names(request) == ["tool_search", "update_plan"]
    # system, user, two calls, their two results, then the reveal: results stay together.
    assert [item.get("type") for item in request["input"][-3:]] == [
        "function_call_output",
        "function_call_output",
        "additional_tools",
    ]
    assert _loaded(request) == [(len(request["input"]) - 1, ["pods"])]


@pytest.mark.parametrize(
    "result",
    [
        # An expanded description of a tool the conversation already had.
        _search_result({"name": "pods", "newly_visible": False}),
        # Output replaced by the context-budget stub: nothing left to read.
        EVICTED_TOOL_RESULT_TEXT,
    ],
    ids=["expanded_only", "result_evicted"],
)
def test_a_tool_no_result_reports_as_newly_visible_stays_in_the_tools_list(
    responses: _Responses, result: str
) -> None:
    # Act
    _invoke_after_reveal(_client(responses), result)

    # Assert
    (request,) = responses.calls
    assert _tool_names(request) == ["tool_search", "update_plan", "pods"]
    assert not _loaded(request)


def test_rejection_retries_with_the_revealed_tool_in_the_tools_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr("core.llm.hosted_credits.admit_hosted_credits", lambda: None)
    responses = _Responses(_bad_request(_ITEM_REJECTION))
    client = _client(responses)

    # Act
    response = _invoke_after_reveal(client)
    _invoke_after_reveal(client)

    # Assert: the retry has the shape requests had before, and it stays that way.
    assert response.content == "ok"
    loading, retried, later = responses.calls
    assert _loaded(loading)
    for request in (retried, later):
        assert _tool_names(request) == ["tool_search", "update_plan", "pods"]
        assert not _loaded(request)


def test_a_loading_request_retries_after_a_concurrent_turn_turned_loading_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyed on what the request carried: one client serves concurrent turns."""
    # Arrange
    monkeypatch.setattr("core.llm.hosted_credits.admit_hosted_credits", lambda: None)
    client: OpenAIAgentClient

    def concurrent_rejection() -> None:
        client._additional_tools_enabled = False

    responses = _Responses(_bad_request(_ITEM_REJECTION), before_error=concurrent_rejection)
    client = _client(responses)

    # Act
    response = _invoke_after_reveal(client)

    # Assert
    assert response.content == "ok"
    assert _loaded(responses.calls[0])
    assert not _loaded(responses.calls[1])


def test_unrelated_bad_request_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setattr("core.llm.hosted_credits.admit_hosted_credits", lambda: None)
    responses = _Responses(_bad_request("context_length_exceeded: the input is too long"))
    client = _client(responses)

    # Act / Assert
    with pytest.raises(RuntimeError, match="request rejected"):
        _invoke_after_reveal(client)
    assert len(responses.calls) == 1
    assert client._additional_tools_enabled


def test_a_request_is_never_left_without_a_declared_tool() -> None:
    # Arrange: the only tool still in the list is the one a search revealed.
    items = [
        {"type": "function_call", "call_id": "call_1", "name": "tool_search", "arguments": "{}"},
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": _search_result({"name": "pods", "newly_visible": True}),
        },
    ]
    tools = [{"type": "function", "name": "pods"}]

    # Act
    request_items, request_tools, loading = additional_tools_request(items, tools, enabled=True)

    # Assert
    assert (request_items, request_tools, loading) == (items, tools, False)


def test_the_transcript_is_never_mutated() -> None:
    """The loop reuses its transcript across iterations and retries."""
    # Arrange
    items = [
        {"type": "function_call", "call_id": "call_1", "name": "tool_search", "arguments": "{}"},
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": json.dumps(_search_result({"name": "pods", "newly_visible": True})),
        },
    ]
    tools = [{"type": "function", "name": "tool_search"}, {"type": "function", "name": "pods"}]
    before = json.dumps([items, tools])

    # Act
    request_items, request_tools, loading = additional_tools_request(items, tools, enabled=True)

    # Assert
    assert loading
    assert [tool["name"] for tool in request_tools] == ["tool_search"]
    assert request_items[:2] == items
    assert json.dumps([items, tools]) == before
