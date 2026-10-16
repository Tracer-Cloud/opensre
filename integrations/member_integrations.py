"""The acting member's GitHub connections for one hosted gateway turn.

A hosted gateway's integration store holds the workspace's connections only.
In the app a member's personal GitHub default outranks the workspace default;
:func:`integrations.github.connections.select_github_connection` applies the
same rule, but only if the turn can see the member's personal grant. This
module asks the webapp for the view of the member taking the turn
(``GET /api/agent/integrations?actor=<slack user id>``) and returns its GitHub
records for that turn alone. Nothing is written to the shared store, so one
member's grant never reaches another member's turn.

It needs the gateway's own org-scoped token: the webapp refuses ``actor`` with
the fleet secret. Everything fails open to ``None`` ("no member view"), which
leaves the workspace connections in charge, as before.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from http import HTTPStatus
from typing import Any

import httpx

from config.account import resolve_account_token
from config.constants.account import (
    OPENSRE_ACCOUNT_INTEGRATIONS_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_INTEGRATIONS_TTL_SECONDS,
)
from config.constants.billing import WEBAPP_URL_ENV
from config.constants.github import GITHUB_PROVENANCE_PARAM
from config.constants.organization import organization_id
from config.principal import PrincipalKind
from config.scope_context import current_scope

logger = logging.getLogger(__name__)

_INTEGRATIONS_PATH = "/api/agent/integrations"
_VIEWER_FIELD = "viewerUserId"
#: Services taken from the member's view. GitHub is the one with personal
#: connections and a per-owner default; the rest stay workspace-wide.
_MEMBER_SERVICES = frozenset({"github"})

_lock = threading.Lock()
#: (organization, actor) -> (fetched_at, records or None for "no member view")
_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]] | None]] = {}
_UNREACHABLE = object()


def load_member_github_records() -> list[dict[str, Any]] | None:
    """The acting member's GitHub records, or ``None`` when there is no member view.

    ``None`` outside a hosted organization turn, without the gateway token,
    when the webapp does not know the member, or when it cannot be reached and
    no earlier view of this member exists. Never raises.
    """
    target = _target()
    if target is None:
        return None
    base_url, org, actor, token = target
    key = (org, actor)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < OPENSRE_ACCOUNT_INTEGRATIONS_TTL_SECONDS:
            return _copy(cached[1])
    fetched = _fetch(base_url=base_url, org=org, actor=actor, token=token)
    with _lock:
        if fetched is _UNREACHABLE:
            stale = _cache.get(key)
            # Keep the last good view; stamp the attempt so an outage is not
            # charged the full timeout on every read.
            records = stale[1] if stale else None
        else:
            records = fetched  # type: ignore[assignment]
        _cache[key] = (now, records)
        return _copy(records)


def member_view_stamp() -> int:
    """Changes when the acting member or their GitHub view changes.

    Part of a session's integration-sources stamp, so a cache resolved for one
    member of a shared Slack thread is never reused for another.
    """
    target = _target()
    if target is None:
        return 0
    records = load_member_github_records()
    canonical = json.dumps([target[2], records], sort_keys=True, default=str)
    return int.from_bytes(hashlib.sha256(canonical.encode("utf-8")).digest()[:8], "big")


def reset_member_integrations_cache() -> None:
    """Forget every member's cached view (deterministic reset hook for tests)."""
    with _lock:
        _cache.clear()


def _copy(records: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    return None if records is None else [dict(record) for record in records]


def _target() -> tuple[str, str, str, str] | None:
    scope = current_scope()
    if scope is None or scope.principal.kind != PrincipalKind.ORG:
        return None
    org = organization_id()
    if not org or scope.principal.id != org:
        return None
    base_url = (os.getenv(WEBAPP_URL_ENV) or "").strip().rstrip("/")
    token = resolve_account_token()
    if not base_url.lower().startswith("https://") or not token:
        return None
    return base_url, org, scope.actor.id, token


def _fetch(*, base_url: str, org: str, actor: str, token: str) -> object:
    """The member's GitHub records, ``None`` without a member view, or ``_UNREACHABLE``."""
    from integrations.webapp_vault import records_from_vault_payload

    try:
        response = httpx.get(
            f"{base_url}{_INTEGRATIONS_PATH}",
            params={"organizationId": org, "actor": actor, GITHUB_PROVENANCE_PARAM: "1"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=OPENSRE_ACCOUNT_INTEGRATIONS_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.debug("[member-integrations] request failed", exc_info=True)
        return _UNREACHABLE
    if response.status_code != HTTPStatus.OK:
        logger.debug("[member-integrations] HTTP %s from the webapp", response.status_code)
        # 403/404: an older webapp, or no gateway token; never a member view.
        return None if response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR else _UNREACHABLE
    try:
        payload = response.json()
    except ValueError:
        return None
    viewer = payload.get(_VIEWER_FIELD) if isinstance(payload, dict) else None
    if not isinstance(viewer, str) or not viewer:
        # The webapp does not know this member: the workspace store already
        # holds everything they may use.
        return None
    records = records_from_vault_payload(payload, organization_id=org, viewer_user_id=viewer)
    if records is None:
        return None
    return [
        record
        for record in records
        if str(record.get("service") or "").strip().lower() in _MEMBER_SERVICES
    ]


__all__ = [
    "load_member_github_records",
    "member_view_stamp",
    "reset_member_integrations_cache",
]
