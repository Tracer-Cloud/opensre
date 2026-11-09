"""Tests for tool trace formatting and redaction helpers."""

from __future__ import annotations

from typing import Any

from infrastructure.observability.trace.redaction import (
    format_json_preview,
    format_tool_trace_entry,
    redact_sensitive,
)

SENSITIVE_KEYS = [
    "api_key",
    "api-key",
    "apiKey",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "auth_header",
]


def test_redact_sensitive_recurses_into_nested_collections() -> None:
    value: dict[str, Any] = {
        **dict.fromkeys(SENSITIVE_KEYS, "do-not-leak"),
        "_internal": object(),
        "backend": object(),
        "mcp_backend": object(),
        "nested": {"token": "secret-token", "safe": "visible"},
        "items": [{"password": "hidden"}, ("public", {"_client": object()})],
    }

    redacted = redact_sensitive(value)

    for key in SENSITIVE_KEYS:
        assert redacted[key] == "[redacted]"
    assert redacted["_internal"] == "[runtime object]"
    assert redacted["backend"] == "[runtime object]"
    assert redacted["mcp_backend"] == "[runtime object]"
    assert redacted["nested"] == {"token": "[redacted]", "safe": "visible"}
    assert redacted["items"] == [
        {"password": "[redacted]"},
        ["public", {"_client": "[runtime object]"}],
    ]


def test_redact_sensitive_handles_scalars_and_regex_precedence() -> None:
    for scalar in (123, "plain"):
        assert redact_sensitive(scalar) == scalar
    assert redact_sensitive(None) is None
    assert redact_sensitive(True) is True
    assert redact_sensitive({"_token": "secret-token"}) == {"_token": "[redacted]"}

    # substring match — "access_token_count" contains "token" -> redacted
    assert redact_sensitive({"access_token_count": "val"}) == {"access_token_count": "[redacted]"}

    # no sensitive substring -> not redacted
    assert redact_sensitive({"count_only": "val"}) == {"count_only": "val"}


def test_format_json_preview_redacts_truncates_and_stringifies() -> None:
    preview = format_json_preview(
        {"query": "service:error", "credential": "super-secret", "count": 2}
    )
    assert '"query": "service:error"' in preview
    assert '"credential": "[redacted]"' in preview
    assert '"count": 2' in preview
    assert "super-secret" not in preview

    truncated = format_json_preview({"message": "x" * 100}, max_chars=50)
    assert len(truncated) <= 50
    assert truncated.endswith("\n... [truncated]")

    stringified = format_json_preview({"values": {1, 2}})
    assert '"values":' in stringified
    assert "1" in stringified
    assert "2" in stringified


def test_format_tool_trace_entry_populates_fields_and_collapses_previews() -> None:
    assert format_tool_trace_entry({"tool_name": "primary", "key": "fallback"}).startswith(
        "- `primary`"
    )
    assert format_tool_trace_entry({"key": "fallback"}).startswith("- `fallback`")
    assert format_tool_trace_entry({}).startswith("- `tool` (iteration None)")
    assert format_tool_trace_entry({"loop_iteration": -1}).startswith("- `tool` (seed)")
    assert format_tool_trace_entry({"loop_iteration": 2}).startswith("- `tool` (iteration 2)")

    entry = {
        "tool_name": "kubernetes_logs",
        "loop_iteration": 3,
        "tool_args": {"namespace": "prod", "token": "hidden"},
        "data": {"status": "ok", "events": [1, 2]},
    }

    formatted = format_tool_trace_entry(entry)

    assert formatted.startswith("- `kubernetes_logs` (iteration 3)")
    assert "namespace" in formatted
    assert "[redacted]" in formatted
    assert "hidden" not in formatted
    assert "status" in formatted
    assert "events" in formatted
    assert formatted.count("\n") == 2


def test_format_tool_trace_entry_handles_empty_trace_record_and_output_limit() -> None:
    formatted = format_tool_trace_entry({})
    assert formatted.startswith("- `tool` (iteration None)")
    assert "\n  input: `{}`" in formatted
    assert "\n  output: `" in formatted
    assert formatted.count("\n") == 2

    limited = format_tool_trace_entry(
        {
            "tool_name": "large_tool",
            "loop_iteration": 1,
            "data": {"output": "x" * 200},
        },
        max_output_chars=60,
    )
    assert limited.startswith("- `large_tool` (iteration 1)")
    assert "... [truncated]" in limited
    assert limited.count("\n") == 2
