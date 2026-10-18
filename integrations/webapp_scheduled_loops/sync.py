"""Keep the webapp's Tasks page and this gateway's scheduled loops in step.

The gateway owns the loops (its scheduler store). The webapp queues the
changes a member asks for on the Tasks page and mirrors the loop list. This
module is the gateway half of that contract (opensre-webapp
``docs/scheduled-loops.md``): it pulls the pending requests, applies each in the
organization's scope with the requesting member as the actor, acknowledges it,
and reports the whole loop list back. Every call uses the gateway's own
org-scoped token; the webapp refuses the fleet secret on these routes.

Delivery is at least once, so a request id is an idempotency key: each
outcome is recorded (:mod:`.request_ledger`) before it is acknowledged, and a
redelivered request is acknowledged again without being applied again. A
created loop also carries its request id, which covers a crash between the
create and the record. Nothing here raises to the caller's thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from typing import Any

import httpx

from config.account import resolve_account_token
from config.constants.billing import WEBAPP_URL_ENV
from config.constants.organization import organization_id
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CREATED_BY_PARAM,
    LOOP_MODE_REPORT,
    LOOP_WEBAPP_REQUEST_ID_PARAM,
)
from infrastructure.scheduling.scheduler.loop_edit import edit_loop, loop_changes
from infrastructure.scheduling.scheduler.loops import (
    LoopSummary,
    create_manual_loop,
    delete_loop,
    resolve_loop_summary,
    set_loop_enabled,
    summarize_loops,
)
from infrastructure.scheduling.scheduler.reload_signal import request_scheduler_reload
from infrastructure.scheduling.scheduler.storage import get_task_store_snapshot
from infrastructure.scheduling.scheduler.task_visibility import visible_to_current_scope
from infrastructure.scheduling.scheduler.types import ScheduledTask
from integrations.webapp_scheduled_loops.request_ledger import (
    LedgerUnavailable,
    RecordedOutcome,
    RequestLedger,
)

logger = logging.getLogger(__name__)

_LOOPS_PATH = "/api/agent/scheduled-loops"
_REQUESTS_PATH = f"{_LOOPS_PATH}/requests"
_TIMEOUT_SECONDS = 10.0
_MAX_REPORTED_LOOPS = 500
#: An unchanged loop list is sent again after this long (the contract's heartbeat).
_HEARTBEAT_SECONDS = 5 * 60
#: Actor for a request that names no member.
_WEBAPP_ACTOR = "webapp"
_UNEXPECTED_FAILURE = "The agent could not apply this change; try again in a minute."
_MISSING_LOOP = "That task no longer exists."
#: Fields an ``update`` request may change (the rest of the loop is kept).
_UPDATE_FIELDS = ("name", "prompt", "cron", "timezone", "mode")


class WebappLoopSync:
    """One organization's request pull, acknowledgements and loop-list report."""

    def __init__(
        self,
        *,
        base_url: str,
        organization: str,
        token: str,
        http: Any = httpx,
        clock: Callable[[], float] = time.monotonic,
        ledger: RequestLedger | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._organization = organization
        self._token = token
        self._http = http
        self._clock = clock
        self._ledger = ledger or RequestLedger()
        self._reported_digest = ""
        self._reported_at: float | None = None

    def sync(self) -> bool:
        """Apply every pending request, then report the list; True when a loop changed.

        A request already applied here is acknowledged with its first outcome
        and never applied again. One that cannot be decided safely (the ledger
        or the task store is unreadable) stays pending for the next pass.
        """
        changed = False
        for request in self._pending_requests():
            request_id = str(request.get("id") or "")
            try:
                outcome = self._ledger.get(self._organization, request_id)
            except LedgerUnavailable:
                logger.warning("[webapp-loops] request ledger unavailable; retrying later")
                break
            if outcome is None:
                outcome = self._apply(request)
                if outcome is None:
                    continue
                changed = changed or outcome.applied
                try:
                    self._ledger.record(self._organization, request_id, outcome)
                except LedgerUnavailable:
                    logger.warning("[webapp-loops] could not record request %s", request_id)
            self._acknowledge(request_id, outcome)
        if changed:
            request_scheduler_reload()
        self.report(force=changed)
        return changed

    def report(self, *, force: bool = False) -> None:
        """Send the loop list when it changed, when forced, or when the heartbeat is due."""
        loops = self._loop_rows()
        if loops is None:
            # A partly unreadable store is not an empty one; never shrink the mirror on it.
            return
        # The digest covers every loop, so a change past the cap still sends a report.
        digest = hashlib.sha256(json.dumps(loops, sort_keys=True).encode()).hexdigest()
        now = self._clock()
        heartbeat_due = self._reported_at is None or now - self._reported_at >= _HEARTBEAT_SECONDS
        if not force and digest == self._reported_digest and not heartbeat_due:
            return
        if len(loops) > _MAX_REPORTED_LOOPS:
            # The web app takes at most this many per report (one request body).
            # Active loops sort first, and ``total`` says the list is partial.
            logger.warning(
                "[webapp-loops] reporting %d of %d loops; the web app accepts at most %d",
                _MAX_REPORTED_LOOPS,
                len(loops),
                _MAX_REPORTED_LOOPS,
            )
        response = self._call(
            "PUT",
            _LOOPS_PATH,
            json={
                "organizationId": self._organization,
                "loops": loops[:_MAX_REPORTED_LOOPS],
                "total": len(loops),
            },
        )
        if response is not None and response.status_code == HTTPStatus.OK:
            self._reported_digest, self._reported_at = digest, now

    def _scope(self, actor: str = _WEBAPP_ACTOR) -> Any:
        return bound_storage_scope(
            StorageScope(principal=Principal.org(self._organization), actor=Actor(id=actor))
        )

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

    def _apply(self, request: Mapping[str, Any]) -> RecordedOutcome | None:
        """The request's outcome, or ``None`` to leave it pending for a later pass."""
        actor = str(request.get("requested_by") or "").strip() or _WEBAPP_ACTOR
        action = str(request.get("action") or "")
        try:
            with self._scope(actor):
                if action == "create":
                    return self._create(str(request["id"]), request.get("payload"))
                if action in ("pause", "resume", "delete"):
                    return self._change(action, str(request.get("loop_id") or ""))
                if action == "update":
                    return self._update(str(request.get("loop_id") or ""), request.get("payload"))
                return RecordedOutcome(False, error=f"Unknown change {action!r}.")
        except ValueError as exc:
            # Our own validation text (bad cron, channel not connected): safe and useful.
            return RecordedOutcome(False, error=str(exc)[:500])
        except Exception:
            logger.exception("[webapp-loops] applying request %s failed", request.get("id"))
            return RecordedOutcome(False, error=_UNEXPECTED_FAILURE)

    def _create(self, request_id: str, raw_payload: Any) -> RecordedOutcome | None:
        # Covers a crash between creating the loop and recording the request.
        snapshot = get_task_store_snapshot()
        if not snapshot.complete:
            # An unreadable row may be this request's loop; creating now could duplicate it.
            return None
        existing = next(
            (
                task.id
                for task in snapshot.tasks
                if task.params.get(LOOP_WEBAPP_REQUEST_ID_PARAM) == request_id
            ),
            None,
        )
        if existing is not None:
            return RecordedOutcome(True, loop_id=existing)
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        channels = payload.get("channels")
        created = create_manual_loop(
            name=str(payload.get("name") or ""),
            prompt=str(payload.get("prompt") or ""),
            cron=str(payload.get("cron") or ""),
            timezone=str(payload.get("timezone") or "UTC"),
            channels=_strings(channels) or None,
            slack_chat_id=str(payload.get("slack_chat_id") or ""),
            telegram_chat_id=str(payload.get("telegram_chat_id") or ""),
            mode=str(payload.get("mode") or LOOP_MODE_REPORT),
            extra_params={LOOP_WEBAPP_REQUEST_ID_PARAM: request_id},
        )
        return RecordedOutcome(True, loop_id=created.task.id)

    def _change(self, action: str, loop_id: str) -> RecordedOutcome:
        # Scoped to this organization: another's loop resolves as missing.
        summary, _error = resolve_loop_summary(loop_id) if loop_id else (None, "")
        if summary is None:
            # Already deleted (a retried request) counts as done for a delete.
            if action == "delete":
                return RecordedOutcome(True, loop_id=loop_id)
            return RecordedOutcome(False, error=_MISSING_LOOP)
        if action == "delete":
            mutation, error = delete_loop(summary.id)
        else:
            mutation, error = set_loop_enabled(summary.id, enabled=action == "resume")
        if mutation is None:
            return RecordedOutcome(False, error=error)
        return RecordedOutcome(True, loop_id=summary.id)

    def _update(self, loop_id: str, raw_payload: Any) -> RecordedOutcome:
        summary, _error = resolve_loop_summary(loop_id) if loop_id else (None, "")
        if summary is None:
            return RecordedOutcome(False, error=_MISSING_LOOP)
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        field = {key: str(payload.get(key) or "") for key in _UPDATE_FIELDS}
        changes = loop_changes(
            name=field["name"],
            prompt=field["prompt"],
            cron=field["cron"],
            timezone=field["timezone"],
            mode=field["mode"],
        )
        mutation, error = edit_loop(summary.task_ids, changes, loop_id=summary.id)
        if mutation is None:
            return RecordedOutcome(False, error=error)
        return RecordedOutcome(True, loop_id=summary.id)

    def _acknowledge(self, request_id: str, outcome: RecordedOutcome) -> None:
        body: dict[str, Any] = {
            "organizationId": self._organization,
            "status": "applied" if outcome.applied else "failed",
        }
        if outcome.loop_id:
            body["loopId"] = outcome.loop_id
        if outcome.error:
            body["error"] = outcome.error
        self._call("POST", f"{_REQUESTS_PATH}/{request_id}", json=body)

    def _loop_rows(self) -> list[dict[str, Any]] | None:
        with self._scope():
            snapshot = get_task_store_snapshot()
            if not snapshot.complete:
                return None
            tasks = [task for task in snapshot.tasks if visible_to_current_scope(task)]
        by_id = {task.id: task for task in tasks}
        return [
            _loop_row(loop, [by_id[task_id] for task_id in loop.task_ids if task_id in by_id])
            for loop in summarize_loops(tasks)
        ]

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
            logger.warning("[webapp-loops] %s %s failed (%s)", method, path, type(exc).__name__)
            return None
        if response.status_code not in (HTTPStatus.OK, HTTPStatus.NOT_FOUND):
            logger.warning("[webapp-loops] %s %s -> %s", method, path, response.status_code)
        return response


def webapp_loop_sync_from_environment() -> WebappLoopSync | None:
    """The sync for this hosted gateway, or ``None`` off a hosted organization.

    Needs the organization id, an https webapp URL, and the gateway token.
    """
    org = organization_id()
    base_url = (os.getenv(WEBAPP_URL_ENV) or "").strip()
    token = resolve_account_token()
    if not org or not token or not base_url.lower().startswith("https://"):
        return None
    return WebappLoopSync(base_url=base_url, organization=org, token=token)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _loop_row(loop: LoopSummary, tasks: Sequence[ScheduledTask]) -> dict[str, Any]:
    first = min(tasks, key=lambda task: task.created_at) if tasks else None
    params = first.params if first is not None else {}
    row: dict[str, Any] = {
        "id": loop.id,
        "name": loop.name,
        "description": loop.description,
        "prompt": loop.prompt,
        "mode": loop.mode,
        "kind": loop.kind.value,
        "cron": loop.cron,
        "timezone": loop.timezone,
        "channels": list(loop.channels),
        "status": loop.status,
        "schedule_error": loop.schedule_error,
        "last_run": loop.last_run,
        "next_run": loop.next_run,
    }
    if first is not None:
        row["created_at"] = first.created_at
    if params.get(LOOP_CREATED_BY_PARAM):
        row["created_by"] = params[LOOP_CREATED_BY_PARAM]
    if params.get(LOOP_WEBAPP_REQUEST_ID_PARAM):
        row["webapp_request_id"] = params[LOOP_WEBAPP_REQUEST_ID_PARAM]
    return row


__all__ = ["WebappLoopSync", "webapp_loop_sync_from_environment"]
