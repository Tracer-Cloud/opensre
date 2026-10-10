"""Authenticated native SigNoz intake, separate from generic text alerts."""

from __future__ import annotations

import hashlib
import secrets
from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from core.domain.alerts.triage.storage import TriageStore
from core.domain.alerts.triage.storage.store import QueueFullError
from integrations.signoz import parse_notification

router = APIRouter()
_basic = HTTPBasic(auto_error=False)


def request_store(request: Request) -> TriageStore:
    """Use a test-injected store or the organization's durable database."""
    return getattr(request.app.state, "triage_store", None) or TriageStore()


@router.post("/alerts/signoz/{source_id}", status_code=HTTPStatus.ACCEPTED)
async def receive_signoz(
    source_id: str,
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> dict[str, object]:
    """Commit occurrence updates before ACK; reject overload for provider retry."""
    store = request_store(request)
    try:
        source = await run_in_threadpool(store.source, source_id)
    except KeyError:
        source = None
    digest = hashlib.sha256((credentials.password if credentials else "").encode()).hexdigest()
    username_ok = secrets.compare_digest(
        credentials.username if credentials else "", source.username if source else "missing"
    )
    password_ok = secrets.compare_digest(digest, source.password_digest if source else "missing")
    if source is None or source.removed or not (credentials and username_ok and password_ok):
        raise HTTPException(
            HTTPStatus.UNAUTHORIZED,
            "Invalid source credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    try:
        events = parse_notification(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid native SigNoz notification") from None
    try:
        ids = await run_in_threadpool(store.ingest, source_id, events)
    except QueueFullError:
        raise HTTPException(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Triage queue full; retry",
            headers={"Retry-After": "30"},
        ) from None
    except ValueError:
        raise HTTPException(HTTPStatus.GONE, "Source removed") from None
    return {"accepted": True, "occurrences": ids}
