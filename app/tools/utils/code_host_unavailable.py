"""Shared unavailable payload helper for code-host investigation tools."""

from __future__ import annotations

from typing import Any


def code_host_unavailable_payload(
    *,
    source: str,
    integration_name: str,
    empty_key: str,
    empty_value: Any,
) -> dict[str, Any]:
    """Return a standardized unavailable payload for code-host tools."""
    return {
        "source": source,
        "available": False,
        "error": f"{integration_name} integration is not configured.",
        empty_key: empty_value,
    }
