"""Forward Streamable HTTP MCP transport across ``mcp`` SDK API shapes."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import cache
from importlib import import_module
from typing import Any

import httpx


@cache
def _load_streamable_http_clients() -> tuple[Any, Any]:
    module = import_module("mcp.client.streamable_http")
    modern_client = getattr(module, "streamable_http_client", None)
    legacy_client = getattr(module, "streamablehttp_client", None)
    if modern_client is None and legacy_client is None:
        raise ImportError("mcp.client.streamable_http has no streamable HTTP client")
    return modern_client, legacy_client


def _as_transport_triple(streams: Any) -> tuple[Any, Any, Any]:
    """Normalize SDK stream tuples to ``(read, write, session_id_callback)``.

    Modern ``mcp`` yields three values. Older clients yield only the two
    streams; callers that unpack exactly three values then fail while opening
    a session.
    """
    try:
        count = len(streams)
    except TypeError:
        count = -1
    if count not in {2, 3}:
        raise ValueError(
            "Streamable HTTP transport returned an unexpected stream count: "
            f"{count if count >= 0 else type(streams).__name__} (expected 2 or 3)."
        )
    read_stream, write_stream, *rest = streams
    return read_stream, write_stream, rest[0] if rest else None


@asynccontextmanager
async def streamable_http_client(
    url: str,
    *,
    http_client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    sse_read_timeout: float = 300.0,
    terminate_on_close: bool = True,
) -> AsyncGenerator[tuple[Any, Any, Any]]:
    modern_client, legacy_client = _load_streamable_http_clients()
    if modern_client is not None:
        del headers, timeout, sse_read_timeout
        async with modern_client(
            url,
            http_client=http_client,
            terminate_on_close=terminate_on_close,
        ) as streams:
            yield _as_transport_triple(streams)
        return

    del http_client
    async with legacy_client(
        url,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
        terminate_on_close=terminate_on_close,
    ) as streams:
        yield _as_transport_triple(streams)
