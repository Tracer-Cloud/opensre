"""Shared unavailable-response builder for MCP bridge tools.

Every MCP bridge tool (``posthog_mcp``, ``sentry_mcp``)
returns the same degraded payload when it can't reach its backend: the base
``tool_unavailable`` envelope, optionally annotated with the ``tool`` that was
being dispatched and the ``arguments`` it was called with. This module holds
that one shape so each bridge doesn't reconstruct it by hand.
"""

from __future__ import annotations

from core.tool_framework.utils.tool_availability import tool_unavailable

__all__ = ["unavailable_response"]


def unavailable_response(
    source: str,
    error: str,
    *,
    tool_name: str | None = None,
    arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the standard unavailable payload for an MCP bridge tool.

    Extends the base ``tool_unavailable`` envelope (``source``/``available``/
    ``error``) with the optional ``tool`` and ``arguments`` keys that a bridge
    attaches when a specific tool call couldn't be dispatched. ``tool`` is added
    only when ``tool_name`` is truthy; ``arguments`` is added whenever it is not
    ``None`` (an empty dict is still recorded).
    """
    payload: dict[str, object] = tool_unavailable(source, error)
    if tool_name:
        payload["tool"] = tool_name
    if arguments is not None:
        payload["arguments"] = arguments
    return payload
