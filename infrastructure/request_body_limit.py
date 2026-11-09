"""Cap request bodies on every mutating route, before anything buffers them.

Pure ASGI rather than a FastAPI dependency: FastAPI parses the body while it
solves a request's parameters, so by the time any dependency or handler runs the
payload is already fully in memory. This sits above routing, rejects an
oversized ``Content-Length`` outright, and counts bytes as the body streams in
so a request that understates or omits its length is bounded too.

Every host that serves a mutating route must install it. All three do:
``build_alert_intake_app``, ``gateway.web.webapp``, and the Slack events
listener in ``build_slack_http_app``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from http import HTTPStatus
from typing import Any

from config.constants.http import MAX_REQUEST_BODY_BYTES

# Local ASGI aliases: starlette ships these but is only a transitive dependency.
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_TOO_LARGE_BODY = b'{"error":"payload too large"}'


class _BodyTooLarge(Exception):
    """Raised out of the wrapped receive once the streamed body passes the cap."""


class RequestBodyLimitMiddleware:
    """Reject bodies over ``max_bytes`` with 413, without buffering them first."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("method", "") not in _BODY_METHODS:
            await self._app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self._max_bytes:
            await _send_too_large(send)
            return

        try:
            await self._app(scope, self._counted(receive), send)
        except _BodyTooLarge:
            # Raised before the app could start a response, so 413 is still ours
            # to send.
            await _send_too_large(send)

    def _counted(self, receive: Receive) -> Receive:
        """Wrap ``receive`` so the running body total cannot pass the cap."""
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > self._max_bytes:
                    raise _BodyTooLarge
            return message

        return limited


def _declared_length(scope: Scope) -> int | None:
    """The request's Content-Length, or None when absent or unparseable."""
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_too_large(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})


__all__ = ["RequestBodyLimitMiddleware"]
