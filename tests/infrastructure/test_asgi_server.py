"""The threaded ASGI server serves any app the caller hands it."""

from __future__ import annotations

import urllib.request
from http import HTTPStatus

from infrastructure.asgi_server import AsgiServerHandle, serve_asgi_in_thread


async def _tiny_app(scope: dict, receive: object, send: object) -> None:
    """A minimal ASGI app that answers 200 OK with a fixed body."""
    assert scope["type"] == "http"
    await send(  # type: ignore[operator]
        {
            "type": "http.response.start",
            "status": int(HTTPStatus.OK),
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"served"})  # type: ignore[operator]


def test_serves_an_injected_app_and_stops_cleanly() -> None:
    # Arrange / Act: the mechanism knows nothing of the gateway — it serves what it is given.
    handle = serve_asgi_in_thread(_tiny_app, host="127.0.0.1", port=0)
    try:
        assert isinstance(handle, AsgiServerHandle)
        assert handle.bound_port > 0
        with urllib.request.urlopen(f"http://{handle.bound_address}/", timeout=5) as resp:
            assert resp.status == HTTPStatus.OK
            assert resp.read() == b"served"
    finally:
        handle.stop()

    # Assert: stop() joins the daemon thread.
    assert not handle.thread.is_alive()
