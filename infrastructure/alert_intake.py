"""HTTP intake for external alerts: ``POST /alerts`` into the process-wide inbox.

A minimal HTTP surface that depends only on the alert domain model, so both the
gateway web app (which mounts this router alongside its own routes) and the
interactive shell (which serves it standalone as its alert listener) host it
without importing each other.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from core.domain.alerts.inbox import (
    AlertInbox,
    IncomingAlert,
    get_current_inbox,
    set_current_inbox,
)
from infrastructure.request_body_limit import RequestBodyLimitMiddleware

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

router = APIRouter()


def _current_inbox() -> AlertInbox:
    """The process-wide inbox; hosts may install their own via set_current_inbox."""
    inbox = get_current_inbox()
    if inbox is None:
        inbox = AlertInbox()
        set_current_inbox(inbox)
    return inbox


def require_local_or_token(request: Request) -> JSONResponse | None:
    """Bearer-token auth when configured; otherwise loopback callers only.

    The trust boundary shared by every mutating endpoint behind the alert
    listener (``/alerts``): a
    configured token, or a local caller.
    """
    token = os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN")
    if token:
        supplied = request.headers.get("authorization", "")
        if hmac.compare_digest(supplied, f"Bearer {token}"):
            return None
        return JSONResponse({"error": "unauthorized"}, status_code=HTTPStatus.UNAUTHORIZED)
    client_host = request.client.host if request.client else ""
    if client_host in _LOOPBACK_HOSTS:
        if _browser_reached_loopback(request):
            return JSONResponse({"error": "forbidden"}, status_code=HTTPStatus.FORBIDDEN)
        return None
    return JSONResponse(
        {"error": "set OPENSRE_ALERT_LISTENER_TOKEN to accept non-loopback callers"},
        status_code=HTTPStatus.FORBIDDEN,
    )


def _url_host(value: str) -> str:
    """Hostname of ``value`` (a URL or ``host[:port]``), lower-cased, brackets stripped."""
    parsed = urlsplit(value if "//" in value else f"//{value}")
    return (parsed.hostname or "").lower()


def _browser_reached_loopback(request: Request) -> bool:
    """Whether a web page, not a local program, sent this loopback request.

    A page on another site can post to ``127.0.0.1`` and arrives with a foreign
    ``Origin``; a DNS-rebound page arrives with a foreign ``Host``. Local tools
    such as ``curl`` send a loopback ``Host`` and no ``Origin``.
    """
    origin = request.headers.get("origin")
    if origin is not None and _url_host(origin) not in _LOOPBACK_HOSTS:
        return True
    return _url_host(request.headers.get("host", "")) not in _LOOPBACK_HOSTS


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/alerts")
async def receive_alert(request: Request) -> JSONResponse:
    if (auth_error := require_local_or_token(request)) is not None:
        return auth_error

    try:
        declared_length = int(request.headers.get("content-length", 0))
    except ValueError:
        return JSONResponse({"error": "invalid Content-Length"}, status_code=HTTPStatus.BAD_REQUEST)
    if declared_length < 0:
        return JSONResponse({"error": "invalid Content-Length"}, status_code=HTTPStatus.BAD_REQUEST)
    # Size is bounded by RequestBodyLimitMiddleware before this handler runs.
    body = await request.body()

    try:
        data = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "invalid json"}, status_code=HTTPStatus.BAD_REQUEST)

    try:
        if not isinstance(data, dict):
            raise TypeError("alert payload must be a JSON object")
        if data.get("received_at") is None:
            data["received_at"] = datetime.now(UTC)
        alert = IncomingAlert.model_validate(data)
    except (TypeError, ValidationError, ValueError) as exc:
        # Expected client error: log the full detail, return only the exception
        # type (payload field names and model internals stay out of the
        # response), and skip Sentry capture for routine 400s.
        logger.warning("Alert payload rejected (%s): %s", type(exc).__name__, exc)
        return JSONResponse(
            {"error": f"invalid alert payload: {type(exc).__name__}"},
            status_code=HTTPStatus.BAD_REQUEST,
        )

    inbox = _current_inbox()
    accepted = inbox.put(alert)
    payload: dict[str, Any] = {"queued": True, "queue_depth": inbox.qsize}
    if not accepted:
        payload["dropped"] = inbox.dropped
        payload["warning"] = "inbox full, oldest alert dropped"
    return JSONResponse(payload, status_code=HTTPStatus.ACCEPTED)


def build_alert_intake_app() -> FastAPI:
    """A standalone app serving only alert intake — the interactive shell's listener.

    Unlike the gateway web app it pulls in no storage or process
    profile — it needs only the shared inbox the host installs.
    """
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware)
    app.include_router(router)
    return app


__all__ = [
    "build_alert_intake_app",
    "require_local_or_token",
    "router",
]
