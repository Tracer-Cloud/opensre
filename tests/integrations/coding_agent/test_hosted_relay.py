"""The loopback relay: a coding agent without a sandbox never holds the account token."""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from integrations.coding_agent.hosted_relay import HostedRouteRelay

_ACCOUNT_TOKEN = "osre_pat_the_organizations_token"


class _Upstream:
    """A stand-in hosted route that records what reached it and streams two chunks back."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        upstream = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
                return None

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                upstream.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("authorization"),
                        "content_type": self.headers.get("content-type"),
                        "body": self.rfile.read(length).decode("utf-8"),
                    }
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"data: first\n\n")
                self.wfile.flush()
                self.wfile.write(b"data: second\n\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/api/llm/v1"


@pytest.fixture
def upstream() -> Iterator[_Upstream]:
    fake = _Upstream()
    fake.thread.start()
    try:
        yield fake
    finally:
        fake.server.shutdown()
        fake.server.server_close()


def _post(url: str, token: str | None, body: dict[str, Any]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    request.add_header("content-type", "application/json")
    if token is not None:
        request.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        return int(error.code), error.read()


def test_the_run_token_opens_the_relay_and_the_account_token_goes_upstream(
    upstream: _Upstream,
) -> None:
    # Arrange
    relay = HostedRouteRelay(upstream.base_url, _ACCOUNT_TOKEN)

    # Act
    with relay:
        status, body = _post(
            f"{relay.base_url}/responses", relay.run_token, {"model": "m", "input": "hi"}
        )

    # Assert: the agent's request reaches the route with the real token; the reply streams back
    assert status == HTTPStatus.OK and body == b"data: first\n\ndata: second\n\n"
    assert relay.run_token != _ACCOUNT_TOKEN and len(relay.run_token) >= 32
    (seen,) = upstream.requests
    assert seen["path"] == "/api/llm/v1/responses"
    assert seen["authorization"] == f"Bearer {_ACCOUNT_TOKEN}"
    assert seen["content_type"] == "application/json"
    assert json.loads(seen["body"]) == {"model": "m", "input": "hi"}


def test_anything_but_the_run_token_is_refused_before_reaching_the_route(
    upstream: _Upstream,
) -> None:
    # Arrange
    relay = HostedRouteRelay(upstream.base_url, _ACCOUNT_TOKEN)

    # Act
    with relay:
        missing, _ = _post(f"{relay.base_url}/responses", None, {})
        wrong, _ = _post(f"{relay.base_url}/responses", "osre_pat_guess", {})
        # Even the real account token is not a key to the relay.
        real, _ = _post(f"{relay.base_url}/responses", _ACCOUNT_TOKEN, {})
        outside, _ = _post(f"{relay.base_url}/../admin", relay.run_token, {})

    # Assert
    assert (missing, wrong, real) == (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.UNAUTHORIZED,
    )
    assert outside == HTTPStatus.NOT_FOUND
    assert upstream.requests == []


def _raw_status(url: str, headers: list[str]) -> int:
    """Send a hand-built request so the Content-Length header can say anything."""
    parts = urllib.parse.urlsplit(url)
    lines = [f"POST {parts.path} HTTP/1.1", f"Host: {parts.netloc}", *headers, "", ""]
    with socket.create_connection((parts.hostname, parts.port), timeout=5) as sock:
        sock.sendall("\r\n".join(lines).encode("ascii"))
        status_line = sock.makefile("rb").readline().decode("ascii")
    return int(status_line.split(" ")[1])


def test_a_bad_content_length_is_refused_before_any_body_is_read(upstream: _Upstream) -> None:
    """A negative, malformed or oversized Content-Length is refused before any body is read."""
    # Arrange
    relay = HostedRouteRelay(upstream.base_url, _ACCOUNT_TOKEN)

    # Act
    with relay:
        url = f"{relay.base_url}/responses"
        auth = f"Authorization: Bearer {relay.run_token}"
        negative = _raw_status(url, [auth, "Content-Length: -1"])
        garbled = _raw_status(url, [auth, "Content-Length: many"])
        oversized = _raw_status(url, [auth, f"Content-Length: {17 * 1024 * 1024}"])

    # Assert: each answered at once with a refusal; nothing reached the route
    assert (negative, garbled) == (HTTPStatus.BAD_REQUEST, HTTPStatus.BAD_REQUEST)
    assert oversized == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert upstream.requests == []


def test_the_relay_is_gone_when_the_run_ends(upstream: _Upstream) -> None:
    # Arrange
    relay = HostedRouteRelay(upstream.base_url, _ACCOUNT_TOKEN)
    with relay:
        url = f"{relay.base_url}/responses"
        token = relay.run_token

    # Act / Assert: the port no longer answers and the base URL is no longer known
    with pytest.raises(urllib.error.URLError):
        _post(url, token, {})
    with pytest.raises(RuntimeError):
        _ = relay.base_url
