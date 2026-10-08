"""The signed-in account's authorized personal and workspace integrations.

A laptop signed in through ``opensre account login`` reads connections from the
OpenSRE app (``GET /api/auth/cli/integrations``, bearer: the account token).
The server authorizes the Clerk user first. This client additionally rejects
owned records outside that user or their active organization.

This is the laptop peer of :mod:`integrations.webapp_vault` (which
authenticates the hosted fleet with ``AGENT_USAGE_SECRET``). Everything here
fails open: a signed-out machine, an unreachable app, or an app without the
route resolves to "no remote integrations" and local sources carry the turn.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from config.account import (
    AccountRecord,
    is_secure_account_origin,
    load_account_record,
    normalize_account_app_url,
    resolve_account_token,
)
from config.constants.account import (
    OPENSRE_ACCOUNT_INTEGRATIONS_PATH,
    OPENSRE_ACCOUNT_INTEGRATIONS_PERSONAL_PARAMS,
    OPENSRE_ACCOUNT_INTEGRATIONS_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_INTEGRATIONS_TTL_SECONDS,
    OPENSRE_ACCOUNT_SERVICE_NAMES,
)
from integrations.credentials_api import CredentialsApiError, validate_integration_store_v2

logger = logging.getLogger(__name__)


@dataclass
class _CacheState:
    """One process-wide snapshot of the remote set, refreshed on read."""

    records: list[dict[str, Any]]
    fingerprint: str
    generation: int
    fetched_at: float
    #: Whether a fetch ever succeeded; a transient failure keeps this snapshot.
    populated: bool
    #: Account origin, Clerk user and active organization; snapshots stay isolated.
    account_scope: tuple[str, str, str] | None


_lock = threading.Lock()
_state = _CacheState(
    records=[],
    fingerprint="",
    generation=0,
    fetched_at=0.0,
    populated=False,
    account_scope=None,
)


def load_account_integrations(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Return authorized integrations as v2 store records; never raises.

    Empty when the machine is signed out, the app is unreachable and no earlier
    snapshot exists, the route is absent (older app), or the response is
    invalid. A fresh-enough snapshot is served without a request. ``refresh``
    asks the app even then, as an expired snapshot would: the snapshot, its
    generation, and the outage fallback are kept, so the generation never
    moves backwards and an unreachable app still serves the last good set.
    """
    record = load_account_record()
    token = resolve_account_token()
    account_scope: tuple[str, str, str] | None = None
    if record is not None and token:
        try:
            origin = normalize_account_app_url(record.app_url)
        except ValueError:
            origin = record.app_url.strip()
        account_scope = (origin, record.user_id, record.organization_id or "")
    now = time.monotonic()
    with _lock:
        if _state.account_scope != account_scope:
            _state.records = []
            _state.fingerprint = ""
            _state.fetched_at = 0.0
            _state.populated = False
            _state.account_scope = account_scope
            _state.generation += 1
        if (
            not refresh
            and _state.populated
            and now - _state.fetched_at < OPENSRE_ACCOUNT_INTEGRATIONS_TTL_SECONDS
        ):
            return [dict(record) for record in _state.records]

    outcome = _fetch(record=record, token=token)
    with _lock:
        if _state.account_scope != account_scope:
            return [dict(cached) for cached in _state.records]
        if outcome.kind == "records":
            _state.fetched_at = now
            _state.populated = True
            if outcome.fingerprint != _state.fingerprint:
                _state.records = outcome.records
                _state.fingerprint = outcome.fingerprint
                _state.generation += 1
        elif outcome.kind in ("unauthorized", "empty"):
            # Unauthorized: the token was revoked or expired, so the last
            # snapshot no longer reflects anything this user may hold.
            # Empty: signed out, no route (older app), or invalid payload.
            _state.fetched_at = now
            _state.populated = True
            if _state.records or _state.fingerprint:
                _state.records = []
                _state.fingerprint = ""
                _state.generation += 1
        else:  # transient: keep the last good snapshot, retry after one TTL.
            # Stamping the failed attempt keeps an outage from charging every
            # read the full fetch timeout.
            _state.fetched_at = now
            _state.populated = True
        return [dict(record) for record in _state.records]


def account_integrations_generation() -> int:
    """A value that changes only when the fetched remote set changes."""
    with _lock:
        return _state.generation


def reset_account_integrations_cache() -> None:
    """Forget the cached snapshot (deterministic reset hook for tests)."""
    global _state
    with _lock:
        _state = _CacheState(
            records=[],
            fingerprint="",
            generation=0,
            fetched_at=0.0,
            populated=False,
            account_scope=None,
        )


@dataclass(frozen=True)
class _FetchOutcome:
    kind: str  # "records" | "empty" | "unauthorized" | "transient"
    records: list[dict[str, Any]]
    fingerprint: str = ""


_EMPTY = _FetchOutcome(kind="empty", records=[])
_TRANSIENT = _FetchOutcome(kind="transient", records=[])


def _fetch(*, record: AccountRecord | None, token: str) -> _FetchOutcome:
    if record is None or not token:
        return _EMPTY

    try:
        app_url = normalize_account_app_url(record.app_url)
    except ValueError:
        return _EMPTY
    if not is_secure_account_origin(app_url):
        return _EMPTY

    try:
        response = httpx.get(
            f"{app_url}{OPENSRE_ACCOUNT_INTEGRATIONS_PATH}",
            # Opt in to this member's personal connections and owner tags; the
            # app keeps the legacy workspace-only shape for clients that don't.
            params=OPENSRE_ACCOUNT_INTEGRATIONS_PERSONAL_PARAMS,
            headers={"Authorization": f"Bearer {token}"},
            timeout=OPENSRE_ACCOUNT_INTEGRATIONS_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.debug("[account-integrations] request failed", exc_info=True)
        return _TRANSIENT

    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return _FetchOutcome(kind="unauthorized", records=[])
    if response.status_code == HTTPStatus.NOT_FOUND:
        # An app older than this CLI has no route; stay local-only.
        return _EMPTY
    if response.status_code != HTTPStatus.OK:
        logger.debug("[account-integrations] HTTP %s from the OpenSRE app", response.status_code)
        return _TRANSIENT

    try:
        payload = response.json()
    except ValueError:
        return _EMPTY
    try:
        store = validate_integration_store_v2(payload)
    except CredentialsApiError:
        logger.debug("[account-integrations] invalid credential set from the OpenSRE app")
        return _EMPTY

    visible = store.visible_to(user_id=record.user_id, organization_id=record.organization_id)
    data: Any = visible.as_store_data()["integrations"]
    records = [_with_cli_service_name(item) for item in data if isinstance(item, dict)]
    return _FetchOutcome(kind="records", records=records, fingerprint=_fingerprint(records))


def _with_cli_service_name(record: dict[str, Any]) -> dict[str, Any]:
    """``record`` under the CLI's service name; merge and classify key on it."""
    service = str(record.get("service") or "").strip().lower()
    renamed = OPENSRE_ACCOUNT_SERVICE_NAMES.get(service)
    return {**record, "service": renamed} if renamed else record


def account_setup_url() -> str | None:
    """The OpenSRE app page where the signed-in principal connects integrations.

    None when the machine is signed out or the app URL is not one the account
    may be sent to.
    """
    record = load_account_record()
    if record is None:
        return None
    try:
        origin = normalize_account_app_url(record.app_url)
    except ValueError:
        return None
    if not is_secure_account_origin(origin):
        return None
    if record.organization_id:
        org = quote(record.organization_id, safe="")
        return f"{origin}/home?org_id={org}"
    return f"{origin}/home"


def _fingerprint(records: list[dict[str, Any]]) -> str:
    canonical = json.dumps(records, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "account_integrations_generation",
    "account_setup_url",
    "load_account_integrations",
    "reset_account_integrations_cache",
]
