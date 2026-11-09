"""Per-request headers on an OpenAI SDK client that serves concurrent turns."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypedDict

import httpx
from openai import DefaultHttpxClient

RequestHeaders = Callable[[], Mapping[str, str]]


class SdkHttpClientKwargs(TypedDict, total=False):
    """The ``OpenAI(...)`` keyword that swaps in a custom HTTP client."""

    http_client: httpx.Client


def http_client_with_request_headers(
    request_headers: RequestHeaders | None,
) -> httpx.Client | None:
    """An SDK HTTP client adding ``request_headers()`` to each request as it is sent.

    LLM clients are cached per role and shared by parallel gateway turns, so a
    header that depends on the turn cannot be a client default: the hook reads
    it on the calling thread, per request. ``None`` keeps the SDK's own client.
    """
    if request_headers is None:
        return None

    def _stamp(request: httpx.Request) -> None:
        request.headers.update(request_headers())

    return DefaultHttpxClient(event_hooks={"request": [_stamp]})


def sdk_http_client_kwargs(http_client: httpx.Client | None) -> SdkHttpClientKwargs:
    """``OpenAI(...)`` kwargs for ``http_client``: none at all when it is unset."""
    return {"http_client": http_client} if http_client is not None else {}


__all__ = [
    "RequestHeaders",
    "SdkHttpClientKwargs",
    "http_client_with_request_headers",
    "sdk_http_client_kwargs",
]
