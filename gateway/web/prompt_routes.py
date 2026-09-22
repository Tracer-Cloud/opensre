"""Remote prompt intake: ``POST /v1/prompt`` queues one prompt, ``GET /v1/prompt/{id}`` reads its result.

The caller is the organization's own control plane (through the OpenSRE app),
authenticated with the same bearer token the alert intake uses. Nothing here
names an organization: the gateway serves exactly one.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.constants.gateway import (
    PROMPT_CONTEXT_MAX_ITEMS,
    PROMPT_CONTEXT_VALUE_MAX_CHARS,
    PROMPT_DEFAULT_ACTOR,
    PROMPT_MAX_CHARS,
    PROMPT_ROUTE_PATH,
)
from gateway.core.prompt_intake.jobs import PromptQueue
from infrastructure.alert_intake import require_local_or_token

router = APIRouter()

_ACTOR_MAX_CHARS = 128


@router.post(PROMPT_ROUTE_PATH)
async def submit_prompt(request: Request) -> JSONResponse:
    if (auth_error := require_local_or_token(request)) is not None:
        return auth_error
    queue = _queue(request)
    if queue is None:
        return _error("prompt_intake_unavailable", HTTPStatus.SERVICE_UNAVAILABLE)
    try:
        payload = await request.json()
    except ValueError:
        return _error("invalid_json", HTTPStatus.BAD_REQUEST)
    if not isinstance(payload, dict):
        return _error("invalid_body", HTTPStatus.BAD_REQUEST)

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _error("prompt_required", HTTPStatus.BAD_REQUEST)
    if len(prompt) > PROMPT_MAX_CHARS:
        return _error("prompt_too_large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    context = _context(payload.get("context"))
    if context is None:
        return _error("invalid_context", HTTPStatus.BAD_REQUEST)
    actor = _actor(payload.get("actor"))
    if actor is None:
        return _error("invalid_actor", HTTPStatus.BAD_REQUEST)

    job = queue.submit(prompt.strip(), context=context, actor=actor)
    if job is None:
        return _error("too_many_prompts", HTTPStatus.SERVICE_UNAVAILABLE)
    return JSONResponse(job.view(), status_code=HTTPStatus.ACCEPTED)


@router.get(PROMPT_ROUTE_PATH + "/{prompt_id}")
def prompt_result(prompt_id: str, request: Request) -> JSONResponse:
    if (auth_error := require_local_or_token(request)) is not None:
        return auth_error
    queue = _queue(request)
    if queue is None:
        return _error("prompt_intake_unavailable", HTTPStatus.SERVICE_UNAVAILABLE)
    job = queue.get(prompt_id)
    if job is None:
        return _error("unknown_prompt", HTTPStatus.NOT_FOUND)
    return JSONResponse(job.view(), status_code=HTTPStatus.OK)


def _queue(request: Request) -> PromptQueue | None:
    """The queue the gateway attached at startup; absent when only the web app runs."""
    queue = getattr(request.app.state, "prompt_queue", None)
    if isinstance(queue, PromptQueue):
        return queue
    return None


def _context(raw: Any) -> dict[str, str] | None:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > PROMPT_CONTEXT_MAX_ITEMS:
        return None
    context: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return None
        if len(value) > PROMPT_CONTEXT_VALUE_MAX_CHARS:
            return None
        context[key] = value
    return context


def _actor(raw: Any) -> str | None:
    if raw is None:
        return PROMPT_DEFAULT_ACTOR
    if not isinstance(raw, str) or not raw.strip() or len(raw) > _ACTOR_MAX_CHARS:
        return None
    return raw.strip()


def _error(code: str, status: HTTPStatus) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


__all__ = ["router"]
