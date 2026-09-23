"""Remote prompt intake: ``POST /v1/prompt`` queues one prompt, ``GET /v1/prompt/{id}`` reads its result.

``POST /v1/prompt/{id}/answer`` answers a prompt that stopped to ask; the answer runs
as a follow-up prompt on the same session and comes back as its own record.

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
from gateway.core.prompt_intake.jobs import AnswerRefused, PromptJob, PromptQueue
from infrastructure.alert_intake import require_local_or_token

router = APIRouter()

_ACTOR_MAX_CHARS = 128


class _Refused(Exception):
    """A request the route turns away; ``code`` and ``status`` are the whole answer."""

    def __init__(self, code: str, status: HTTPStatus) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@router.post(PROMPT_ROUTE_PATH)
async def submit_prompt(request: Request) -> JSONResponse:
    if (auth_error := require_local_or_token(request)) is not None:
        return auth_error
    try:
        queue = _ready_queue(request)
        payload = await _json_object(request)
        job = _submitted(queue, payload)
    except _Refused as refused:
        return _error(refused.code, refused.status)
    return JSONResponse(job.view(), status_code=HTTPStatus.ACCEPTED)


@router.get(PROMPT_ROUTE_PATH + "/{prompt_id}")
def prompt_result(prompt_id: str, request: Request) -> JSONResponse:
    if (auth_error := require_local_or_token(request)) is not None:
        return auth_error
    try:
        queue = _ready_queue(request)
        job = _known_job(queue, prompt_id)
    except _Refused as refused:
        return _error(refused.code, refused.status)
    return JSONResponse(job.view(), status_code=HTTPStatus.OK)


@router.post(PROMPT_ROUTE_PATH + "/{prompt_id}/answer")
async def answer_prompt(prompt_id: str, request: Request) -> JSONResponse:
    if (auth_error := require_local_or_token(request)) is not None:
        return auth_error
    try:
        queue = _ready_queue(request)
        parent = _known_job(queue, prompt_id)
        payload = await _json_object(request)
        follow_up = _answered(queue, parent, payload)
    except _Refused as refused:
        return _error(refused.code, refused.status)
    return JSONResponse(follow_up.view(), status_code=HTTPStatus.ACCEPTED)


def _submitted(queue: PromptQueue, payload: dict[str, Any]) -> PromptJob:
    prompt = _prompt(payload.get("prompt"))
    context = _context(payload.get("context"))
    actor = _actor(payload.get("actor"))
    job = queue.submit(prompt, context=context, actor=actor)
    if job is None:
        raise _Refused("too_many_prompts", HTTPStatus.SERVICE_UNAVAILABLE)
    return job


def _answered(queue: PromptQueue, parent: PromptJob, payload: dict[str, Any]) -> PromptJob:
    answer = _answer(payload.get("answer"))
    try:
        follow_up = queue.answer(parent, answer)
    except AnswerRefused as refused:
        raise _Refused(refused.code, HTTPStatus.CONFLICT) from None
    if follow_up is None:
        raise _Refused("too_many_prompts", HTTPStatus.SERVICE_UNAVAILABLE)
    return follow_up


def _ready_queue(request: Request) -> PromptQueue:
    """The queue the gateway attached at startup; absent when only the web app runs."""
    queue = getattr(request.app.state, "prompt_queue", None)
    if not isinstance(queue, PromptQueue):
        raise _Refused("prompt_intake_unavailable", HTTPStatus.SERVICE_UNAVAILABLE)
    return queue


def _known_job(queue: PromptQueue, prompt_id: str) -> PromptJob:
    job = queue.get(prompt_id)
    if job is None:
        raise _Refused("unknown_prompt", HTTPStatus.NOT_FOUND)
    return job


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        raise _Refused("invalid_json", HTTPStatus.BAD_REQUEST) from None
    if not isinstance(payload, dict):
        raise _Refused("invalid_body", HTTPStatus.BAD_REQUEST)
    return payload


def _prompt(raw: Any) -> str:
    return _text_field(raw, missing="prompt_required")


def _answer(raw: Any) -> str:
    return _text_field(raw, missing="answer_required")


def _text_field(raw: Any, *, missing: str) -> str:
    """A non-empty string within the prompt size limit, stripped."""
    if not isinstance(raw, str) or not raw.strip():
        raise _Refused(missing, HTTPStatus.BAD_REQUEST)
    if len(raw) > PROMPT_MAX_CHARS:
        raise _Refused("prompt_too_large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    return raw.strip()


def _context(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > PROMPT_CONTEXT_MAX_ITEMS:
        raise _Refused("invalid_context", HTTPStatus.BAD_REQUEST)
    context: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise _Refused("invalid_context", HTTPStatus.BAD_REQUEST)
        if len(value) > PROMPT_CONTEXT_VALUE_MAX_CHARS:
            raise _Refused("invalid_context", HTTPStatus.BAD_REQUEST)
        context[key] = value
    return context


def _actor(raw: Any) -> str:
    if raw is None:
        return PROMPT_DEFAULT_ACTOR
    if not isinstance(raw, str) or not raw.strip() or len(raw) > _ACTOR_MAX_CHARS:
        raise _Refused("invalid_actor", HTTPStatus.BAD_REQUEST)
    return raw.strip()


def _error(code: str, status: HTTPStatus) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


__all__ = ["router"]
