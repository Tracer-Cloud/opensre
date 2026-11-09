"""``POST /v1/loops/sync``: apply the web app's loop changes now instead of at the next poll.

The gateway pulls the Loops page's requests on a slow poll so an idle fleet
costs nothing (``WEBAPP_LOOP_SYNC_INTERVAL_SECONDS``). When a member saves,
runs, pauses or deletes a loop, the app asks the organization's control plane
to call this route, which wakes the sync thread at once; the request itself is
still pulled from the app, so this carries no data and repeats are harmless.

The caller is the control plane, authenticated with the same bearer token as
the alert and prompt intake.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.constants.gateway import LOOP_SYNC_ROUTE_PATH
from infrastructure.alert_intake import require_local_or_token

router = APIRouter()


@router.post(LOOP_SYNC_ROUTE_PATH)
async def sync_loops_now(request: Request) -> JSONResponse:
    if (auth_error := require_local_or_token(request)) is not None:
        return auth_error
    wake = getattr(request.app.state, "loop_sync_wake", None)
    if not callable(wake) or wake() is False:
        return JSONResponse(
            {"error": "loop_sync_unavailable"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE
        )
    return JSONResponse({"status": "syncing"}, status_code=HTTPStatus.ACCEPTED)


__all__ = ["router"]
