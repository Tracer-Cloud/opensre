"""Tests for the shared tool-unavailable payload helper."""

from __future__ import annotations

from core.tool_framework.utils.tool_availability import (
    envelope_failure_state,
    envelope_source_id,
    is_tool_unavailable_envelope,
    tool_access_denied,
    tool_missing_config,
    tool_missing_data,
    tool_no_match,
    tool_transport_failure,
    tool_unavailable,
)


def test_tool_unavailable_base_shape() -> None:
    payload = tool_unavailable("helm", "helm integration is not configured.")

    assert payload == {
        "source": "helm",
        "available": False,
        "error": "helm integration is not configured.",
        "failure_state": "missing_config",
    }
    assert is_tool_unavailable_envelope(payload) is True
    assert envelope_source_id(payload) == "helm"


def test_is_tool_unavailable_envelope_rejects_lookalikes() -> None:
    """Ordinary values / nested flags must not count as config failure."""
    assert is_tool_unavailable_envelope(None) is False
    assert is_tool_unavailable_envelope("unauthorized") is False
    assert is_tool_unavailable_envelope({"available": False}) is False  # no error
    assert is_tool_unavailable_envelope({"available": False, "error": ""}) is False
    assert is_tool_unavailable_envelope({"available": False, "error": 401}) is False
    assert is_tool_unavailable_envelope({"available": True, "error": "x"}) is False
    # Nested feature-flag shape — not top-level typed envelope.
    assert (
        is_tool_unavailable_envelope({"flags": {"unauthorized_banner": {"available": False}}})
        is False
    )


def test_tool_unavailable_merges_extra_fields() -> None:
    payload = tool_unavailable("groundcover", "query failed", data=[], summary={}, truncated=False)

    assert payload == {
        "source": "groundcover",
        "available": False,
        "error": "query failed",
        "failure_state": "missing_config",
        "data": [],
        "summary": {},
        "truncated": False,
    }


def test_tool_unavailable_unrelated_extra_field_preserved() -> None:
    payload = tool_unavailable("posthog_mcp", "not configured", tool="list_flags")

    assert payload["source"] == "posthog_mcp"
    assert payload["available"] is False
    assert payload["error"] == "not configured"
    assert payload["tool"] == "list_flags"


def test_tool_failure_states_are_distinct_and_typed() -> None:
    failures = (
        (tool_no_match("github", "none"), "no_match"),
        (tool_missing_config("github", "configure it"), "missing_config"),
        (tool_access_denied("github", "forbidden"), "denied"),
        (tool_transport_failure("github", "timed out"), "transport_failure"),
        (tool_missing_data("github", "empty"), "missing_data"),
    )

    assert [envelope_failure_state(payload) for payload, _state in failures] == [
        state for _payload, state in failures
    ]


def test_tool_unavailable_extra_can_override_base_fields() -> None:
    """extra is merged in after the base fields, so a same-named kwarg wins.

    This is intentional (see the tool_unavailable docstring) and documented
    here so the override behavior doesn't get "fixed" by accident later.
    """
    payload = tool_unavailable("groundcover", "query failed", available=True)

    assert payload["available"] is True
