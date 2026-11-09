"""Which web app member an actor id names, so a loop's owner is recognized on every surface.

One person acts under their Slack user id in Slack and their web app user id in
the app and its web sessions. The web app resolves either to a current member
of this organization (``GET /api/agent/members``), the same way it resolves
``actor`` for integrations. Answers are cached for a few minutes; a failure
answers ``None`` ("unknown"), which can only hide a private loop, never show one.
"""

from __future__ import annotations

import logging
import threading
import time
from http import HTTPStatus

from integrations.webapp_requests.client import WebappAgentClient, webapp_agent_target

logger = logging.getLogger(__name__)

_MEMBERS_PATH = "/api/agent/members"
_LOG_TAG = "member-identity"
_TIMEOUT_SECONDS = 5.0
#: How long a resolved member is reused; a miss is asked again sooner (they may sign in).
_MEMBER_TTL_SECONDS = 300.0
_MISS_TTL_SECONDS = 60.0

_lock = threading.Lock()
#: (organization, actor) -> (fetched_at, web app user id or None)
_cache: dict[tuple[str, str], tuple[float, str | None]] = {}
_UNREACHABLE = object()


def member_user_id(actor: str) -> str | None:
    """The web app user id of the member ``actor`` names here, or ``None``. Never raises."""
    actor = actor.strip()
    target = webapp_agent_target()
    if not actor or target is None:
        return None
    key = (target.organization, actor)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        fetched_at, member = cached
        if now - fetched_at < (_MEMBER_TTL_SECONDS if member else _MISS_TTL_SECONDS):
            return member
    fetched = _fetch(
        WebappAgentClient(target, log_tag=_LOG_TAG, timeout_seconds=_TIMEOUT_SECONDS), actor
    )
    if fetched is _UNREACHABLE:
        # Keep the last answer through an outage rather than forget a member.
        return cached[1] if cached is not None else None
    member = fetched if isinstance(fetched, str) else None
    with _lock:
        _cache[key] = (now, member)
    return member


def reset_member_identity_cache() -> None:
    """Forget every cached answer (deterministic reset hook for tests)."""
    with _lock:
        _cache.clear()


def _fetch(client: WebappAgentClient, actor: str) -> object:
    """The member's web app user id, ``None`` when the actor names no member, or ``_UNREACHABLE``."""
    try:
        response = client.call(
            "GET", _MEMBERS_PATH, params={"organizationId": client.organization, "actor": actor}
        )
    except Exception:
        logger.debug("[member-identity] request failed", exc_info=True)
        return _UNREACHABLE
    if response is None:
        return _UNREACHABLE
    if response.status_code == HTTPStatus.NOT_FOUND:
        # A web app without the route: no member is known by another id.
        return None
    if response.status_code != HTTPStatus.OK:
        return _UNREACHABLE
    try:
        data = response.json().get("data")
    except (ValueError, AttributeError):
        return _UNREACHABLE
    user_id = data.get("userId") if isinstance(data, dict) else None
    return user_id if isinstance(user_id, str) and user_id else None


__all__ = ["member_user_id", "reset_member_identity_cache"]
