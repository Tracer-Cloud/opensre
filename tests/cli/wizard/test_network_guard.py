"""Regression tests for the wizard socket guard.

Two directions are pinned:

* outbound connections (``connect`` / ``connect_ex`` / ``create_connection``)
  must still be blocked — the guard's original purpose;
* loopback connections — specifically the one ``ProactorEventLoop`` makes to
  build its self-pipe — must be allowed, so prompt tests work on Windows.

The autouse fixture in ``conftest.py`` is active in every test in this module,
so these calls exercise the real guard rather than a copy of it.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from .conftest import LiveNetworkCallBlocked


@pytest.mark.parametrize(
    "operation",
    [
        lambda: socket.socket().connect(("example.com", 80)),
        lambda: socket.socket().connect_ex(("example.com", 80)),
        lambda: socket.create_connection(("example.com", 80)),
    ],
    ids=["connect", "connect_ex", "create_connection"],
)
def test_outbound_connections_are_still_blocked(operation):
    with pytest.raises(LiveNetworkCallBlocked):
        operation()


def test_loopback_connect_is_allowed():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        with socket.socket() as client:
            client.connect(("127.0.0.1", port))
            accepted, _ = listener.accept()
            with accepted:
                assert client.getpeername()[0] == "127.0.0.1"


def test_loopback_ipv6_and_hostname_are_recognized():
    from .conftest import _is_loopback

    for address in ("127.0.0.1", "127.42.1.9", "::1", "[::1]", "localhost"):
        assert _is_loopback((address, 1234)), address
    for address in ("example.com", "8.8.8.8", "192.168.1.1"):
        assert not _is_loopback((address, 1234)), address


def test_asyncio_event_loop_can_be_created_under_guard():
    """Windows ProactorEventLoop needs a loopback socketpair; Unix does not."""
    loop = asyncio.new_event_loop()
    try:
        assert loop is not None
    finally:
        loop.close()
