"""Keep the web app's Knowledge page and this gateway's knowledge in step.

The gateway owns the knowledge (memory stores and workspace entries on the
organization's volume). This is the gateway half of the contract in
opensre-webapp ``docs/knowledge.md``, the same shape as the Tasks page sync:

1. ``PUT /api/agent/knowledge`` — the whole mirror, sent within seconds of a
   memory or workspace entry being written here, and on a heartbeat;
2. ``GET /api/agent/knowledge/requests`` — changes members asked for in the
   app (forget one of their memories; admins: delete a workspace entry,
   rescan an integration);
3. ``POST /api/agent/knowledge/requests/{id}`` — the outcome of each.

Delivery is at least once, so a request id is an idempotency key recorded
before it is acknowledged. Every call uses the gateway's org-scoped token.
Nothing here raises to the caller's thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from http import HTTPStatus
from typing import Any

import httpx

import config.constants.paths as paths
from config.constants.gateway import (
    WEBAPP_KNOWLEDGE_CHANGE_CHECK_SECONDS,
    WEBAPP_KNOWLEDGE_SYNC_INTERVAL_SECONDS,
)
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.domain.knowledge import (
    delete_workspace_entry,
    knowledge_enabled,
    workspace_entry_exists,
)
from core.domain.memory import (
    delete_memory,
    is_valid_slug,
    knowledge_generation,
    member_memory_dir,
    memory_path,
    memory_root,
    slugify,
)
from integrations.webapp_knowledge.snapshot import build_snapshot
from integrations.webapp_requests import (
    LedgerUnavailable,
    RecordedOutcome,
    RequestLedger,
    WebappAgentClient,
    WebappAgentTarget,
    webapp_agent_target,
)

logger = logging.getLogger(__name__)

_KNOWLEDGE_PATH = "/api/agent/knowledge"
_REQUESTS_PATH = f"{_KNOWLEDGE_PATH}/requests"
_LEDGER_FILENAME = "webapp_knowledge_requests.json"
_LOG_TAG = "webapp-knowledge"
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
        self._organization = organization
        self._client = WebappAgentClient(
            WebappAgentTarget(
                base_url=base_url.rstrip("/"), organization=organization, token=token
            ),
            log_tag=_LOG_TAG,
            http=http,
        )
        self._request_rescan = request_rescan
        self._scan_status = scan_status
        self._clock = clock
        self._ledger = ledger or RequestLedger(
            paths.OPENSRE_HOME_DIR / _LEDGER_FILENAME, log_tag=_LOG_TAG
        )
        self._reported_digest = ""
        self._reported_at: float | None = None
        #: The knowledge generation the last report was built from.
        self._reported_generation: int | None = None
        #: Outcomes applied here that the ledger could not take yet, by request id.
        self._unrecorded: dict[str, RecordedOutcome] = {}

    def sync(self) -> bool:
        """Apply every pending request, then report; ``True`` when something changed.

        A request is acknowledged only once its outcome is in the ledger: an
        acknowledged request whose outcome was lost could be applied again on
        redelivery and delete a memory saved since. An outcome the ledger
        cannot take yet is held in memory and offered to the ledger again on
        the next delivery instead of being re-applied. A change that could not
        be applied for a passing reason (the store was locked) stays pending.
        """
        changed = False
        pending = self._client.pending_requests(_REQUESTS_PATH)
        if pending is not None:
            # A request the app no longer delivers is settled there: drop what we held for it.
            delivered = {str(request.get("id") or "") for request in pending}
            self._unrecorded = {
                rid: outcome for rid, outcome in self._unrecorded.items() if rid in delivered
            }
        for request in pending or []:
            request_id = str(request.get("id") or "")
            outcome = self._unrecorded.get(request_id)
            recorded = False
            if outcome is None:
                try:
                    outcome = self._ledger.get(self._organization, request_id)
                except LedgerUnavailable:
                    logger.warning("[webapp-knowledge] request ledger unavailable; retrying later")
                    break
                recorded = outcome is not None
            if outcome is None:
                outcome = self._apply(request)
                if outcome is None:
                    continue
                changed = changed or outcome.applied
            if not recorded and not self._record(request_id, outcome):
                continue
            self._acknowledge(request_id, outcome)
        self.report(force=changed)
        return changed

    def _record(self, request_id: str, outcome: RecordedOutcome) -> bool:
        """Save ``outcome`` in the ledger; ``False`` (and held in memory) when it cannot be."""
        try:
            self._ledger.record(self._organization, request_id, outcome)
        except LedgerUnavailable:
            logger.warning("[webapp-knowledge] could not record request %s; holding it", request_id)
            self._unrecorded[request_id] = outcome
            return False
        self._unrecorded.pop(request_id, None)
        return True

    def report(self, *, force: bool = False) -> None:
        """Send the mirror when it changed, when forced, or when the heartbeat is due."""
        # Read before the stores: a write that lands while they are read moves
        # the generation again, so the next change check reports it. A failed
        # send is retried by the next pass or change, not every check.
        self._reported_generation = knowledge_generation()
        with self._scope():
            snapshot = build_snapshot(self._organization, scans=self._scans())
        # The snapshot is built in a fixed order, so its JSON needs no sorting to compare.
        stable = {key: value for key, value in snapshot.items() if key != "generatedAt"}
        digest = hashlib.sha256(json.dumps(stable).encode()).hexdigest()
        now = self._clock()
        heartbeat_due = self._reported_at is None or now - self._reported_at >= HEARTBEAT_SECONDS
        if not force and digest == self._reported_digest and not heartbeat_due:
            return
        response = self._client.call("PUT", _KNOWLEDGE_PATH, json=snapshot)
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

    def _scope(self) -> Any:
        return bound_storage_scope(
            StorageScope(principal=Principal.org(self._organization), actor=Actor(_SYNC_ACTOR))
        )

    def _apply(self, request: Mapping[str, Any]) -> RecordedOutcome | None:
        """The request's outcome, or ``None`` to leave it pending for a later pass."""
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
        body: dict[str, Any] = {"status": "applied" if outcome.applied else "failed"}
        if outcome.error:
            body["error"] = outcome.error
        self._client.acknowledge(_REQUESTS_PATH, request_id, body)

    def run_forever(
        self,
        stopped: threading.Event,
        *,
        interval_seconds: float = WEBAPP_KNOWLEDGE_SYNC_INTERVAL_SECONDS,
        change_check_seconds: float = WEBAPP_KNOWLEDGE_CHANGE_CHECK_SECONDS,
    ) -> None:
        """Sync until ``stopped``: a full pass every ``interval_seconds``.

        Between passes, every ``change_check_seconds``, a memory or workspace
        entry written in this process since the last report is reported at
        once, so what a member tells the agent reaches the Knowledge page in
        seconds. A check that finds nothing new reads no store.
        """
        next_pass = self._clock()
        while not stopped.is_set():
            try:
                if self._clock() >= next_pass:
                    next_pass = self._clock() + interval_seconds
                    self.sync()
                elif knowledge_generation() != self._reported_generation:
                    self.report()
            except Exception as exc:  # noqa: BLE001 - keep syncing after one bad pass
                logger.warning("[%s] sync pass failed (%s)", _LOG_TAG, type(exc).__name__)
            stopped.wait(change_check_seconds)


def _forget_memory(actor: str, name: str) -> RecordedOutcome | None:
    """Delete one of ``actor``'s memories; ``None`` when it is still there (a locked store).

    A memory already gone counts as forgotten.
    """
    slug = slugify(name)
    if not actor or not is_valid_slug(slug):
        return RecordedOutcome(False, error="Name the memory to forget.")
    directory = member_memory_dir(actor)
    if directory is None:
        return RecordedOutcome(True)
    with memory_root(directory):
        if delete_memory(slug) or not memory_path(slug).exists():
            return RecordedOutcome(True)
    logger.warning("[webapp-knowledge] could not delete a memory yet; retrying later")
    return None


def _delete_entry(name: str) -> RecordedOutcome | None:
    """Delete one workspace entry; ``None`` when it is still there (a locked store).

    An entry already gone counts as deleted.
    """
    slug = slugify(name)
    if not is_valid_slug(slug):
        return RecordedOutcome(False, error="Name the entry to delete.")
    if delete_workspace_entry(slug) or not workspace_entry_exists(slug):
        return RecordedOutcome(True)
    logger.warning("[webapp-knowledge] could not delete a workspace entry yet; retrying later")
    return None


def webapp_knowledge_sync_from_environment(
    *,
    request_rescan: RequestRescan | None = None,
    scan_status: Callable[[], list[dict[str, Any]]] | None = None,
) -> WebappKnowledgeSync | None:
    """The sync for this hosted gateway, or ``None`` off a hosted organization."""
    if not knowledge_enabled():
        return None
    target = webapp_agent_target()
    if target is None:
        return None
    return WebappKnowledgeSync(
        base_url=target.base_url,
        organization=target.organization,
        token=target.token,
        request_rescan=request_rescan,
        scan_status=scan_status,
    )


__all__ = [
    "HEARTBEAT_SECONDS",
    "RequestRescan",
    "WebappKnowledgeSync",
    "webapp_knowledge_sync_from_environment",
]
