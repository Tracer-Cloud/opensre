"""The newest message must carry a cache breakpoint so history accrues hits.

Tools and system are already marked; without a breakpoint in ``messages`` the
growing conversation — the bulk of a ReAct loop's input — is re-billed at full
price every iteration. Mark the last content block of the most recently
appended turn.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from core.llm.transports.sdk.agent_clients import (
    AnthropicAgentClient,
    _anthropic_messages_with_cache,
)
from tests.utils.anthropic_streams import streaming_messages


def _client_with_capture() -> tuple[AnthropicAgentClient, MagicMock]:
    client = AnthropicAgentClient.__new__(AnthropicAgentClient)
    client._model = "claude-opus-5"
    client._max_tokens = 1024
    sdk = MagicMock()
    sdk.messages.create.return_value = MagicMock(content=[], stop_reason="end_turn", usage=None)
    # The request streams; the stream replays what ``create`` returns.
    client._client = SimpleNamespace(messages=streaming_messages(sdk.messages.create))
    return client, sdk


# ── Unit: the marker helper ─────────────────────────────────────────────────


def test_string_content_becomes_a_marked_text_block() -> None:
    # Arrange
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "first"},
        {"role": "user", "content": "newest"},
    ]

    # Act
    marked = _anthropic_messages_with_cache(messages)

    # Assert: only the newest message is marked; earlier ones untouched.
    assert marked[0] == {"role": "user", "content": "first"}
    (block,) = marked[-1]["content"]
    assert block["type"] == "text"
    assert block["text"] == "newest"
    assert block["cache_control"] == {"type": "ephemeral"}


def test_block_list_content_marks_only_the_last_block() -> None:
    # Arrange: a tool-result turn, the common ReAct shape.
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "one"},
                {"type": "tool_result", "tool_use_id": "b", "content": "two"},
            ],
        }
    ]

    # Act
    marked = _anthropic_messages_with_cache(messages)

    # Assert
    first, last = marked[-1]["content"]
    assert "cache_control" not in first
    assert last["cache_control"] == {"type": "ephemeral"}


def test_input_is_not_mutated() -> None:
    """The caller's message list must stay reusable across retries."""
    # Arrange
    original: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]

    # Act
    _anthropic_messages_with_cache(original)

    # Assert
    assert original == [{"role": "user", "content": "hello"}]


def test_empty_and_unmarkable_content_is_left_alone() -> None:
    # Arrange / Act / Assert: nothing to mark → structure unchanged.
    assert _anthropic_messages_with_cache([]) == []
    weird: list[dict[str, Any]] = [{"role": "user", "content": ""}]
    assert _anthropic_messages_with_cache(weird) == weird


# ── Integration: the invoke path sends the marker ───────────────────────────


def test_invoke_marks_the_newest_message() -> None:
    # Arrange
    client, sdk = _client_with_capture()

    # Act
    client.invoke(
        messages=[
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ],
        system="be helpful",
    )

    # Assert
    sent = sdk.messages.create.call_args.kwargs["messages"]
    assert sent[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert sent[0] == {"role": "user", "content": "q1"}


# ── Determinism: identical inputs must serialize byte-identically ───────────


def test_tool_schemas_serialize_deterministically() -> None:
    """A varying tools byte-stream would invalidate the cached prefix silently."""
    # Arrange
    import json

    from core.llm.transports.sdk.agent_clients import _anthropic_tool_schema

    class _Tool:
        name = "grep"
        description = "search"
        public_input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}

    # Act: two independent builds of the same tool list.
    first = json.dumps([_anthropic_tool_schema(_Tool())], sort_keys=False)
    second = json.dumps([_anthropic_tool_schema(_Tool())], sort_keys=False)

    # Assert
    assert first == second


def test_invoke_response_carries_cache_tokens() -> None:
    """The agent path must surface cache counters, not just the log line."""
    # Arrange
    client, sdk = _client_with_capture()
    sdk.messages.create.return_value = SimpleNamespace(
        content=[],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=3,
            output_tokens=4,
            cache_read_input_tokens=8118,
            cache_creation_input_tokens=0,
        ),
    )

    # Act
    response = client.invoke(messages=[{"role": "user", "content": "hi"}])

    # Assert
    assert response.cache_read_tokens == 8118
    assert response.cache_creation_tokens == 0


def test_multi_block_content_shares_no_block_dicts_with_the_input() -> None:
    """Every block in the marked copy must be a fresh dict.

    The transcript is reused across ReAct iterations; a shared block dict
    means a later in-place mutation of the API payload writes through into
    live history.
    """
    # Arrange
    first = {"type": "tool_result", "tool_use_id": "a", "content": "one"}
    second = {"type": "tool_result", "tool_use_id": "b", "content": "two"}
    messages: list[dict[str, Any]] = [{"role": "user", "content": [first, second]}]

    # Act
    marked = _anthropic_messages_with_cache(messages)

    # Assert: no aliasing anywhere in the marked message.
    marked_first, marked_last = marked[-1]["content"]
    assert marked_first is not first
    assert marked_last is not second
    marked_first["content"] = "MUTATED"
    assert first["content"] == "one"
