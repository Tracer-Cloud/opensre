"""Query runner for groundcover gcQL signal tools."""

from __future__ import annotations

from typing import Any, cast

from integrations.groundcover.client import GroundcoverClient
from integrations.groundcover.envelope import build_envelope, needs_query, unavailable
from integrations.groundcover.params import time_range


def run_signal_query(
    *,
    source: str,
    mcp_tool: str,
    client: GroundcoverClient | None,
    query: str,
    start: str = "",
    end: str = "",
    period: str = "",
    backend: Any = None,
    extra_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared runner for gcQL signal tools (logs/traces/events/issues/apm).

    ``client`` is a pre-built :class:`GroundcoverClient` (or None) injected via
    ``extract_params``; credentials never travel through the model-facing tool
    arguments. When ``backend`` is provided (synthetic harness), the call
    short-circuits to the fixture backend. An empty query yields a cheap
    ``needs_query`` envelope without any MCP round trip.
    """
    if backend is not None:
        method = getattr(backend, mcp_tool, None)
        if callable(method):
            return cast(
                "dict[str, Any]",
                method(query=query, start=start, end=end, period=period),
            )
        return unavailable(source, f"groundcover backend does not implement {mcp_tool}")

    if client is None:
        return unavailable(source, "groundcover integration not configured")
    if not query.strip():
        return needs_query(source)

    args: dict[str, Any] = {"query": query}
    if start:
        args["start"] = start
    if end:
        args["end"] = end
    if period:
        args["period"] = period
    if extra_args:
        args.update(extra_args)

    result = client.call_tool(mcp_tool, args)
    return build_envelope(source, query, result, tr=time_range(start, end, period))
