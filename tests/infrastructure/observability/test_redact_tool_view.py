"""Redact tool input/output once and share the immutable view."""

from __future__ import annotations

from infrastructure.observability.trace.redaction import (
    RedactedToolView,
    redact_sensitive,
    redact_tool_view,
)


def test_redact_tool_view_redacts_input_and_output_once() -> None:
    tool_input = {"namespace": "prod", "token": "secret-a"}
    output = {"logs": ["ok"], "password": "secret-b", "nested": {"api_key": "k"}}

    view = redact_tool_view(tool_input, output)

    assert isinstance(view, RedactedToolView)
    assert view.tool_input == {"namespace": "prod", "token": "[redacted]"}
    assert view.output == {
        "logs": ["ok"],
        "password": "[redacted]",
        "nested": {"api_key": "[redacted]"},
    }
    # Shared view matches standalone redact (same observable shape).
    assert view.tool_input == redact_sensitive(tool_input)
    assert view.output == redact_sensitive(output)


def test_redact_tool_view_output_optional() -> None:
    view = redact_tool_view({"token": "x"})
    assert view.tool_input == {"token": "[redacted]"}
    assert view.output is None


def test_redact_sensitive_skips_non_containers() -> None:
    """Scalars are returned as-is — no copy, no walk."""
    for scalar in (None, True, 0, 3.14, "plain", b"bytes"):
        assert redact_sensitive(scalar) is scalar


def test_a_token_count_is_kept_beside_a_redacted_token() -> None:
    """A count named after tokens is not a credential; hiding it rewrote replayed tool calls."""
    redacted = redact_sensitive({"command": "ls", "max_output_tokens": 4000, "auth_token": "s3"})

    assert redacted == {"command": "ls", "max_output_tokens": 4000, "auth_token": "[redacted]"}
