"""Tests for the shared MCP bridge unavailable-response builder."""

from __future__ import annotations

from core.tool_framework.utils.mcp_bridge import unavailable_response


def test_base_envelope_without_optional_fields() -> None:
    result = unavailable_response("posthog_mcp", "not configured")
    assert result == {
        "source": "posthog_mcp",
        "available": False,
        "error": "not configured",
        "failure_state": "missing_config",
    }


def test_source_is_passed_through() -> None:
    assert unavailable_response("sentry_mcp", "boom")["source"] == "sentry_mcp"
    assert unavailable_response("posthog_mcp", "boom")["source"] == "posthog_mcp"


def test_tool_name_added_when_truthy() -> None:
    result = unavailable_response("x_mcp", "failed", tool_name="post_tweet")
    assert result["tool"] == "post_tweet"


def test_tool_name_omitted_when_none_or_empty() -> None:
    assert "tool" not in unavailable_response("x_mcp", "failed")
    assert "tool" not in unavailable_response("x_mcp", "failed", tool_name=None)
    assert "tool" not in unavailable_response("x_mcp", "failed", tool_name="")


def test_arguments_added_when_provided() -> None:
    args = {"conversation_id": "abc"}
    result = unavailable_response("posthog_mcp", "failed", arguments=args)
    assert result["arguments"] == args


def test_empty_arguments_dict_is_still_recorded() -> None:
    # ``arguments={}`` is distinct from ``arguments=None``: the empty dict is kept.
    result = unavailable_response("posthog_mcp", "failed", arguments={})
    assert result["arguments"] == {}


def test_arguments_omitted_when_none() -> None:
    assert "arguments" not in unavailable_response("posthog_mcp", "failed", arguments=None)


def test_all_fields_together() -> None:
    result = unavailable_response(
        "sentry_mcp",
        "tool call failed",
        tool_name="get_issue",
        arguments={"issue_id": "42"},
    )
    assert result == {
        "source": "sentry_mcp",
        "available": False,
        "error": "tool call failed",
        "failure_state": "missing_config",
        "tool": "get_issue",
        "arguments": {"issue_id": "42"},
    }
