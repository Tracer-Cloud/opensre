"""Tests for shared code-host unavailable payload helper."""

from __future__ import annotations

from app.tools.utils.code_host_unavailable import code_host_unavailable_payload


def test_code_host_unavailable_payload_shape() -> None:
    payload = code_host_unavailable_payload(
        source="github",
        integration_name="GitHub MCP",
        empty_key="matches",
        empty_value=[],
    )

    assert payload == {
        "source": "github",
        "available": False,
        "error": "GitHub MCP integration is not configured.",
        "matches": [],
    }
