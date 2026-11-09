"""Shared unavailable payload helper for code-host tools."""

from __future__ import annotations

from typing import Any

from core.tool_framework.utils.tool_availability import tool_unavailable


def code_host_unavailable_payload(
    *,
    source: str,
    integration_name: str,
    empty_key: str,
    empty_value: Any,
) -> dict[str, Any]:
    """Return a standardized unavailable payload for code-host tools."""
    return tool_unavailable(
        source,
        f"{integration_name} integration is not configured.",
        **{empty_key: empty_value},
    )
