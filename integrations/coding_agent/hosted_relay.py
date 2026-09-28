"""Loopback relay that keeps the organization's account token out of a coding agent.

An agent that runs without its own sandbox could read its environment and send it
anywhere. So it never sees the account token: it talks to this relay on the
loopback interface with a token minted for one run, and the relay adds the real
token when it forwards the call to the hosted route. When the run ends the
relay stops and the run token is worthless.
"""

from __future__ import annotations

import http.client
import logging
import posixpath
import secrets
import threading
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any

logger = logging.getLogger(__name__)

_LOOPBACK = "127.0.0.1"
_ROUTE_PREFIX = "/v1"
_CHUNK_BYTES = 8192
_FORWARDED_REQUEST_HEADERS = ("content-type", "accept", "openai-beta")
_FORWARDED_RESPONSE_HEADERS = ("content-type", "x-request-id")
_MAX_BODY_BYTES = 16 * 1024 * 1024
_UPSTREAM_TIMEOUT_SECONDS = 600.0


class HostedRouteRelay:
    """One run's private door to the hosted LLM route.

    ``base_url`` and ``run_token`` are what the agent gets; the upstream token
    stays in this process.
    """

    def __init__(self, upstream_base_url: str, upstream_token: str) -> None:
        self._upstream_base_url = upstream_base_url.rstrip("/")
        self._upstream_token = upstream_token
        self.run_token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("the relay is not running")
        port = self._server.server_address[1]
        return f"http://{_LOOPBACK}:{port}{_ROUTE_PREFIX}"

    def __enter__(self) -> HostedRouteRelay:
        relay = self
        upstream = self._upstream_base_url
        upstream_token = self._upstream_token
        run_token = self.run_token

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
                # Request lines carry nothing worth a log line; bodies never are logged.
                return None

            def do_POST(self) -> None:  # noqa: N802
                self._relay()

            def do_GET(self) -> None:  # noqa: N802
                self._relay()

            def _relay(self) -> None:
                if not _bearer_matches(self.headers.get("authorization"), run_token):
                    self._reply_status(HTTPStatus.UNAUTHORIZED)
                    return
                route_path = _route_path(self.path)
                if route_path is None:
                    self._reply_status(HTTPStatus.NOT_FOUND)
                    return
                length = _declared_body_length(self.headers.get("content-length"))
                if length is None:
                    self._reply_status(HTTPStatus.BAD_REQUEST)
                    return
                if length > _MAX_BODY_BYTES:
                    self._reply_status(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                    return
                body = self.rfile.read(length) if length else None
                target = upstream + route_path
                request = urllib.request.Request(target, data=body, method=self.command)
                for name in _FORWARDED_REQUEST_HEADERS:
                    value = self.headers.get(name)
                    if value:
                        request.add_header(name, value)
                request.add_header("authorization", f"Bearer {upstream_token}")
                try:
                    response = urllib.request.urlopen(  # noqa: S310 (https route from the account)
                        request, timeout=_UPSTREAM_TIMEOUT_SECONDS
                    )
                except urllib.error.HTTPError as error:
                    response = error
                except (urllib.error.URLError, http.client.HTTPException, OSError):
                    logger.warning("hosted route relay: upstream unreachable", exc_info=True)
                    self._reply_status(HTTPStatus.BAD_GATEWAY)
                    return
                with response:
                    self._stream(response)

            def _stream(self, response: Any) -> None:
                self.send_response(int(response.status))
                for name in _FORWARDED_RESPONSE_HEADERS:
                    value = response.headers.get(name)
                    if value:
                        self.send_header(name, value)
                self.send_header("transfer-encoding", "chunked")
                self.send_header("connection", "close")
                self.end_headers()
                # read1 hands over whatever has arrived, so streamed events are not held
                # back until a full buffer fills; an error response lacks it and is small.
                read_available = getattr(response, "read1", response.read)
                while True:
                    chunk = read_available(_CHUNK_BYTES)
                    if not chunk:
                        break
                    self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                self.close_connection = True

            def _reply_status(self, status: HTTPStatus) -> None:
                self.send_response(int(status))
                self.send_header("content-length", "0")
                self.send_header("connection", "close")
                self.end_headers()
                self.close_connection = True

        server = ThreadingHTTPServer((_LOOPBACK, 0), _Handler)
        server.daemon_threads = True
        thread = threading.Thread(
            target=server.serve_forever, name="hosted-route-relay", daemon=True
        )
        thread.start()
        relay._server = server
        relay._thread = thread
        return relay

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        server = self._server
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._server = None
        self._thread = None


def _declared_body_length(header: str | None) -> int | None:
    """The Content-Length as a non-negative int; ``None`` when absent-but-garbled or negative.

    A negative or malformed length would make ``read`` wait for the connection to
    close while the body grows without bound, so it is refused before any read.
    """
    if header is None or not header.strip():
        return 0
    try:
        length = int(header.strip())
    except ValueError:
        return None
    if length < 0:
        return None
    return length


def _route_path(request_path: str) -> str | None:
    """The part after ``/v1`` for a path that stays inside the route, else ``None``."""
    path_only, _, query = request_path.partition("?")
    normalized = posixpath.normpath(path_only)
    if not normalized.startswith(_ROUTE_PREFIX + "/"):
        return None
    inside = normalized[len(_ROUTE_PREFIX) :]
    return f"{inside}?{query}" if query else inside


def _bearer_matches(header: str | None, expected: str) -> bool:
    if not header:
        return False
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return secrets.compare_digest(presented.strip(), expected)


__all__ = ["HostedRouteRelay"]
