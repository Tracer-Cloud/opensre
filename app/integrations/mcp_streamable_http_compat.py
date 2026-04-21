"""Forward Streamable HTTP MCP transport to the current ``mcp`` SDK.

Older code paths used the deprecated ``streamablehttp_client(..., httpx_client_factory=...)``
with a factory that returned a context-manager stand-in. That pattern breaks with current
``mcp`` because ``async with client`` does not rebind ``client`` to the inner
``httpx.AsyncClient``, so the transport received ``_DetachExitAsyncClientCM`` instead of a
real client (``AttributeError: ... has no attribute 'stream'``).

The supported API is ``streamable_http_client(url, http_client=prebuilt_client)``.
Extra kwargs below are accepted for call-site compatibility but are ignored: configure
timeouts and headers on ``httpx.AsyncClient`` before calling.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.client.streamable_http import (  # type: ignore[import-not-found]
    streamable_http_client as _mcp_streamable_http_client,
)


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
    del headers, timeout, sse_read_timeout
    async with _mcp_streamable_http_client(
        url,
        http_client=http_client,
        terminate_on_close=terminate_on_close,
    ) as triple:
        yield triple
