"""Legacy streamable-HTTP clients yield two streams; callers unpack three."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from integrations import mcp_streamable_http_compat as compat


def _clear_client_cache() -> None:
    loader = compat._load_streamable_http_clients
    if hasattr(loader, "cache_clear"):
        loader.cache_clear()


@pytest.fixture(autouse=True)
def _reset_client_cache() -> None:
    _clear_client_cache()
    yield
    _clear_client_cache()


@asynccontextmanager
async def _yield_streams(streams: Any):
    yield streams


@pytest.mark.asyncio
async def test_legacy_two_stream_result_normalizes_to_a_triple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read, write = object(), object()

    captured: dict[str, Any] = {}

    def _legacy_only() -> tuple[Any, Any]:
        def _client(*_args: Any, **kwargs: Any):
            captured.update(kwargs)
            return _yield_streams((read, write))

        return None, _client

    monkeypatch.setattr(compat, "_load_streamable_http_clients", _legacy_only)

    compat_client = MagicMock()
    async with compat.streamable_http_client(
        "https://mcp.example.test/mcp",
        http_client=compat_client,
    ) as triple:
        assert triple == (read, write, None)

    factory = captured["httpx_client_factory"]
    borrowed = factory()
    assert borrowed.send == compat_client.send


def test_as_transport_triple_rejects_unexpected_stream_counts() -> None:
    with pytest.raises(ValueError, match="expected 2 or 3"):
        compat._as_transport_triple((object(),))
