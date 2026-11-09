"""Tests for Bedrock Converse schema normalization and message helpers."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest

from core.llm.transports.sdk.bedrock_converse import (
    build_assistant_tool_use_message,
    build_converse_tool_specs,
    build_tool_result_message,
    map_bedrock_client_error,
    new_tool_use_id,
    normalize_tool_input_schema,
    parse_converse_output,
    to_converse_messages,
)
from core.llm.types import ToolCall
from infrastructure.safety.guardrails.apply import apply_guardrails_to_converse_payload


@pytest.fixture(autouse=True)
def _reset_tool_registry() -> Generator[None]:
    from tools.registry import clear_tool_registry_cache

    clear_tool_registry_cache()
    yield
    clear_tool_registry_cache()


def test_normalize_empty_schema_is_object_with_properties() -> None:
    assert normalize_tool_input_schema({}) == {"type": "object", "properties": {}}
    assert normalize_tool_input_schema(None) == {"type": "object", "properties": {}}


def test_build_converse_tool_specs_coerces_non_object_root() -> None:
    import types

    tool = types.SimpleNamespace(
        name="query_logs",
        description="Query logs",
        public_input_schema={"type": "array"},
    )
    specs = build_converse_tool_specs([tool])
    json_schema = specs[0]["toolSpec"]["inputSchema"]["json"]
    assert json_schema == {"type": "object", "properties": {}}


def test_build_converse_tool_specs_preserves_object_properties() -> None:
    import types

    tool = types.SimpleNamespace(
        name="query_logs",
        description="Query logs",
        public_input_schema={
            "type": "object",
            "properties": {
                "tags": {"type": "array"},
                "query": {"type": "string"},
            },
        },
    )
    json_schema = build_converse_tool_specs([tool])[0]["toolSpec"]["inputSchema"]["json"]
    assert json_schema["type"] == "object"
    assert json_schema["properties"]["tags"]["items"] == {"type": "string"}


def test_to_converse_messages_converts_string_content() -> None:
    converted = to_converse_messages([{"role": "user", "content": "hello"}])
    assert converted == [{"role": "user", "content": [{"text": "hello"}]}]


def test_apply_guardrails_wraps_string_content_in_text_blocks() -> None:
    from unittest.mock import MagicMock, patch

    engine = MagicMock()
    engine.is_active = True
    engine.apply.side_effect = lambda text: f"guarded:{text}"

    with patch(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator", return_value=engine
    ):
        messages, system = apply_guardrails_to_converse_payload(
            messages=[{"role": "user", "content": "hello"}],
            system="sys",
        )

    assert messages == [{"role": "user", "content": [{"text": "guarded:hello"}]}]
    assert system == "guarded:sys"


def test_build_assistant_tool_use_message() -> None:
    calls = [ToolCall(id="abc123456", name="ping", input={"x": 1})]
    msg = build_assistant_tool_use_message(calls)
    assert msg["role"] == "assistant"
    assert msg["content"][0]["toolUse"]["toolUseId"] == "abc123456"


def test_build_tool_result_message_error_status() -> None:
    msg = build_tool_result_message(
        [ToolCall(id="id1", name="fail", input={})],
        [{"error": "timeout"}],
    )
    tr = msg["content"][0]["toolResult"]
    assert tr["status"] == "error"
    assert tr["content"] == [{"json": {"error": "timeout"}}]


def test_build_tool_result_message_serializes_datetime() -> None:
    dt = datetime(2026, 5, 22, 12, 0, 0)
    msg = build_tool_result_message(
        [ToolCall(id="id2", name="t", input={})],
        [{"timestamp": dt}],
    )
    payload = msg["content"][0]["toolResult"]["content"][0]["json"]
    assert isinstance(payload["timestamp"], str)


def test_parse_converse_output_text_and_tool_use() -> None:
    response = {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "Checking logs."},
                    {
                        "toolUse": {
                            "toolUseId": "tu1",
                            "name": "query_logs",
                            "input": {"q": "err"},
                        }
                    },
                ],
            }
        },
    }
    text, tool_calls, stop_reason, raw = parse_converse_output(response)
    assert text == "Checking logs."
    assert stop_reason == "tool_use"
    assert tool_calls == [("tu1", "query_logs", {"q": "err"})]
    assert raw["role"] == "assistant"


def test_new_tool_use_id_is_short_alphanumeric() -> None:
    tool_id = new_tool_use_id()
    assert len(tool_id) == 10
    assert tool_id.isalnum()


def test_map_bedrock_client_error_validation() -> None:
    import botocore.exceptions

    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "ValidationException", "Message": "missing field type"}},
        "Converse",
    )
    mapped = map_bedrock_client_error("mistral.test", err)
    assert "HTTP 400" in str(mapped)


def test_map_bedrock_client_error_throttling() -> None:
    import botocore.exceptions

    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        "Converse",
    )
    mapped = map_bedrock_client_error("mistral.test", err)
    assert "rate limit" in str(mapped).lower()
