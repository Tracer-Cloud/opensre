"""Keep the web app's Knowledge page and this gateway's knowledge in step.

The gateway owns the knowledge (memory stores and workspace entries on the
organization's volume). This is the gateway half of the contract in
opensre-webapp ``docs/knowledge.md``, the same shape as the Tasks page sync:

1. ``PUT /api/agent/knowledge`` — the whole mirror, sent when it changed and
   on a heartbeat;
2. ``GET /api/agent/knowledge/requests`` — changes members asked for in the
   app (forget one of their memories; admins: delete a workspace entry,
   rescan an integration);
3. ``POST /api/agent/knowledge/requests/{id}`` — the outcome of each.

Delivery is at least once, so a request id is an idempotency key recorded
before it is acknowledged. Every call uses the gateway's org-scoped token.
Nothing here raises to the caller's thread.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping
from http import HTTPStatus
from typing import Any

import httpx

import config.constants.paths as paths
from config.account import resolve_account_token
from config.constants.billing import WEBAPP_URL_ENV
from config.constants.organization import organization_id
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.domain.knowledge import delete_workspace_entry, knowledge_enabled
from core.domain.memory import delete_memory, member_memory_dir, memory_root, slugify
from integrations.webapp_knowledge.snapshot import build_snapshot
from integrations.webapp_scheduled_loops.request_ledger import (
    LedgerUnavailable,
    RecordedOutcome,
    RequestLedger,
)

logger = logging.getLogger(__name__)

_KNOWLEDGE_PATH = "/api/agent/knowledge"
_REQUESTS_PATH = f"{_KNOWLEDGE_PATH}/requests"
_LEDGER_FILENAME = "webapp_knowledge_requests.json"
_TIMEOUT_SECONDS = 15.0
#: An unchanged mirror is sent again after this long.
HEARTBEAT_SECONDS = 10 * 60
_SYNC_ACTOR = "webapp-knowledge"
_UNEXPECTED_FAILURE = "The agent could not apply this change; try again in a minute."

#: Asks the scan service to rescan a service; ``False`` for an unknown one.
RequestRescan = Callable[[str], bool]


class WebappKnowledgeSync:
    """One organization's knowledge mirror, request pull and acknowledgements."""

    def __init__(
        self,
        *,
        base_url: str,
        organization: str,
        token: str,
        request_rescan: RequestRescan | None = None,
        scan_status: Callable[[], list[dict[str, Any]]] | None = None,
        http: Any = httpx,
        clock: Callable[[], float] = time.monotonic,
        ledger: RequestLedger | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._organization = organization
        self._token = token
        self._request_rescan = request_rescan
        self._scan_status = scan_status
        self._http = http
        self._clock = clock
        self._ledger = ledger or RequestLedger(paths.OPENSRE_HOME_DIR / _LEDGER_FILENAME)
        self._reported_digest = ""
        self._reported_at: float | None = None

    def sync(self) -> bool:
        """Apply every pending request, then report; ``True`` when something changed."""
        changed = False
        for request in self._pending_requests():
            request_id = str(request.get("id") or "")
            try:
                outcome = self._ledger.get(self._organization, request_id)
            except LedgerUnavailable:
                logger.warning("[webapp-knowledge] request ledger unavailable; retrying later")
                break
            if outcome is None:
                outcome = self._apply(request)
                changed = changed or outcome.applied
                try:
                    self._ledger.record(self._organization, request_id, outcome)
                except LedgerUnavailable:
                    logger.warning("[webapp-knowledge] could not record request %s", request_id)
            self._acknowledge(request_id, outcome)
        self.report(force=changed)
        return changed

    def report(self, *, force: bool = False) -> None:
        """Send the mirror when it changed, when forced, or when the heartbeat is due."""
        with self._scope():
            snapshot = build_snapshot(self._organization, scans=self._scans())
        stable = {key: value for key, value in snapshot.items() if key != "generatedAt"}
        digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        now = self._clock()
        heartbeat_due = self._reported_at is None or now - self._reported_at >= HEARTBEAT_SECONDS
        if not force and digest == self._reported_digest and not heartbeat_due:
            return
        response = self._call("PUT", _KNOWLEDGE_PATH, json=snapshot)
        if response is not None and response.status_code == HTTPStatus.OK:
            self._reported_digest, self._reported_at = digest, now

    def _scans(self) -> list[dict[str, Any]]:
        if self._scan_status is None:
            return []
        try:
            return self._scan_status()
        except Exception:  # noqa: BLE001 - scan status is decoration on the mirror
            logger.debug("[webapp-knowledge] scan status unavailable", exc_info=True)
            return []

    @contextlib.contextmanager
    def _scope(self) -> Iterator[None]:
        scope = StorageScope(principal=Principal.org(self._organization), actor=Actor(_SYNC_ACTOR))
        with bound_storage_scope(scope):
            yield

    def _pending_requests(self) -> list[Mapping[str, Any]]:
        response = self._call("GET", _REQUESTS_PATH, params={"organizationId": self._organization})
        if response is None or response.status_code != HTTPStatus.OK:
            return []
        try:
            data = response.json().get("data")
        except (ValueError, AttributeError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, Mapping) and item.get("id")]

    def _apply(self, request: Mapping[str, Any]) -> RecordedOutcome:
        action = str(request.get("action") or "")
        payload = request.get("payload")
        fields = payload if isinstance(payload, Mapping) else {}
        try:
            with self._scope():
                if action == "forget_memory":
                    return _forget_memory(
                        str(request.get("actor") or ""), str(fields.get("name") or "")
                    )
                if action == "delete_entry":
                    return _delete_entry(str(fields.get("name") or ""))
                if action == "rescan":
                    return self._rescan(str(fields.get("service") or ""))
                return RecordedOutcome(False, error=f"Unknown change {action!r}.")
        except Exception:
            logger.exception("[webapp-knowledge] applying request %s failed", request.get("id"))
            return RecordedOutcome(False, error=_UNEXPECTED_FAILURE)

    def _rescan(self, service: str) -> RecordedOutcome:
        if self._request_rescan is None:
            return RecordedOutcome(False, error="Scans are not running on this agent.")
        if not self._request_rescan(service):
            return RecordedOutcome(
                False, error=f"{service or 'That integration'} cannot be scanned yet."
            )
        return RecordedOutcome(True)

    def _acknowledge(self, request_id: str, outcome: RecordedOutcome) -> None:
        body: dict[str, Any] = {
            "organizationId": self._organization,
            "status": "applied" if outcome.applied else "failed",
        }
        if outcome.error:
            body["error"] = outcome.error
        self._call("POST", f"{_REQUESTS_PATH}/{request_id}", json=body)

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(
                method,
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=_TIMEOUT_SECONDS,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            logger.warning("[webapp-knowledge] %s %s failed (%s)", method, path, type(exc).__name__)
            return None
        if response.status_code not in (HTTPStatus.OK, HTTPStatus.NOT_FOUND):
            logger.warning("[webapp-knowledge] %s %s -> %s", method, path, response.status_code)
        return response


def _forget_memory(actor: str, name: str) -> RecordedOutcome:
    """Delete one of ``actor``'s memories; an already-missing one counts as forgotten."""
    slug = slugify(name)
    if not actor or not slug:
        return RecordedOutcome(False, error="Name the memory to forget.")
    directory = member_memory_dir(actor)
    if directory is None:
        return RecordedOutcome(True)
    with memory_root(directory):
        delete_memory(slug)
    return RecordedOutcome(True)


def _delete_entry(name: str) -> RecordedOutcome:
    """Delete one workspace entry; an already-missing one counts as deleted."""
    slug = slugify(name)
    if not slug:
        return RecordedOutcome(False, error="Name the entry to delete.")
    delete_workspace_entry(slug)
    return RecordedOutcome(True)


def webapp_knowledge_sync_from_environment(
    *,
    request_rescan: RequestRescan | None = None,
    scan_status: Callable[[], list[dict[str, Any]]] | None = None,
) -> WebappKnowledgeSync | None:
    """The sync for this hosted gateway, or ``None`` off a hosted organization."""
    if not knowledge_enabled():
        return None
    org = organization_id()
    base_url = (os.getenv(WEBAPP_URL_ENV) or "").strip()
    token = resolve_account_token()
    if not org or not token or not base_url.lower().startswith("https://"):
        return None
    return WebappKnowledgeSync(
        base_url=base_url,
        organization=org,
        token=token,
        request_rescan=request_rescan,
        scan_status=scan_status,
    )


__all__ = [
    "HEARTBEAT_SECONDS",
    "RequestRescan",
    "WebappKnowledgeSync",
    "webapp_knowledge_sync_from_environment",
]
