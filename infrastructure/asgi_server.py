"""Run an ASGI app in a background thread.

Generic transport infrastructure: the caller supplies the app, so this module
never depends on any particular web surface. Both the gateway daemon and the
interactive shell serve their app through this one mechanism — one app, one port
per host process; ``port=0`` binds an ephemeral free port.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import uvicorn

from config.constants.gateway import WEB_STOP_TIMEOUT_SECONDS


@dataclass
class AsgiServerHandle:
    """A running uvicorn server plus the thread and address it bound to."""

    server: uvicorn.Server
    thread: threading.Thread
    bound_host: str
    bound_port: int

    @property
    def bound_address(self) -> str:
        return f"{self.bound_host}:{self.bound_port}"

    def stop(self, *, timeout: float = WEB_STOP_TIMEOUT_SECONDS) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=timeout)


def serve_asgi_in_thread(
    app: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    startup_timeout: float = 10.0,
) -> AsgiServerHandle:
    """Start uvicorn serving ``app`` in a daemon thread and wait until it is bound."""
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None))
    thread = threading.Thread(target=server.run, name="asgi-server", daemon=True)
    thread.start()

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if server.started and server.servers:
            bound = server.servers[0].sockets[0].getsockname()
            return AsgiServerHandle(server, thread, str(bound[0]), int(bound[1]))
        if not thread.is_alive():
            break
        time.sleep(0.05)
    raise RuntimeError(f"ASGI app on {host}:{port} failed to start")


__all__ = ["AsgiServerHandle", "serve_asgi_in_thread"]
