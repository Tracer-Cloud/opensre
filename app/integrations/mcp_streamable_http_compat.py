"""Shim for ``streamable_http_client(url, http_client=...)`` with current ``mcp`` SDK.

``mcp.client.streamable_http.streamablehttp_client`` does not accept a pre-built
``httpx.AsyncClient``. This module wraps the official helper and injects the caller's
client via ``httpx_client_factory`` without closing it when the transport exits.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
from mcp.client.streamable_http import (  # type: ignore[import-not-found]
    streamablehttp_client as _streamablehttp_client,
)


class _DetachExitAsyncClientCM:
    """Yields an existing client under ``async with`` without closing it on exit."""

    __slots__ = ("_client",)

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *_args: object) -> None:
        return None


def _httpx_factory_for_client(
    http_client: httpx.AsyncClient,
) -> Callable[
    [dict[str, str] | None, httpx.Timeout | None, httpx.Auth | None],
    httpx.AsyncClient,
]:
    def _factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        del headers, timeout, auth
        return cast(httpx.AsyncClient, _DetachExitAsyncClientCM(http_client))

    return _factory


@asynccontextmanager
async def streamable_http_client(
    url: str,
    *,
    http_client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    sse_read_timeout: float = 300.0,
    terminate_on_close: bool = True,
) -> AsyncGenerator[
    tuple[Any, Any, Any],
    None,
]:
    async with _streamablehttp_client(
        url,
        headers=headers or {},
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
        terminate_on_close=terminate_on_close,
        httpx_client_factory=_httpx_factory_for_client(http_client),  # type: ignore[arg-type]
    ) as triple:
        yield triple
