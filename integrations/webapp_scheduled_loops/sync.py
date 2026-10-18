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
create and the record. A ``run`` is recorded before its run starts, so a crash
can lose a run but never repeat one. Nothing here raises to the caller's thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

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
from infrastructure.scheduling.scheduler.runner import run_task_now
from infrastructure.scheduling.scheduler.storage import get_task_store_snapshot, has_live_claim
from infrastructure.scheduling.scheduler.task_visibility import visible_to_current_scope
from infrastructure.scheduling.scheduler.types import ScheduledTask
from integrations.webapp_scheduled_loops.request_ledger import (
    LedgerUnavailable,
    RecordedOutcome,
    RequestLedger,
)

if TYPE_CHECKING:
    from infrastructure.scheduling.scheduler.runners import SchedulerRunners

logger = logging.getLogger(__name__)

_LOOPS_PATH = "/api/agent/scheduled-loops"
_REQUESTS_PATH = f"{_LOOPS_PATH}/requests"
_TIMEOUT_SECONDS = 10.0
_MAX_REPORTED_LOOPS = 500
#: An unchanged loop list is sent again after this long (the contract's heartbeat).
_HEARTBEAT_SECONDS = 30 * 60
#: Actor for a request that names no member.
_WEBAPP_ACTOR = "webapp"
_UNEXPECTED_FAILURE = "The agent could not apply this change; try again in a minute."
_MISSING_LOOP = "That task no longer exists."
_RUN_IN_PROGRESS = "This task is already running; try again when it finishes."
_RUN_UNAVAILABLE = "This agent cannot run tasks on demand."
_RUN_ACTION = "run"
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
        runners: SchedulerRunners | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._organization = organization
        self._token = token
        self._http = http
        self._clock = clock
        self._ledger = ledger or RequestLedger()
        self._reported_digest = ""
        self._reported_at: float | None = None
        # The poll thread and a scheduler reload may report at the same time.
        self._lock = threading.Lock()
        #: Runs scheduled ticks dispatch through; ``None`` refuses ``run`` requests.
        self._runners = runners
        self._runs_lock = threading.Lock()
        #: Loops with a ``run`` request in flight here, from acceptance to its end.
        self._running: set[str] = set()
        #: Threads running accepted ``run`` requests, so shutdown can wait for them.
        self._run_threads: set[threading.Thread] = set()
        #: Set once shutdown drains runs; later ``run`` requests stay pending.
        self._closing = False

    def sync(self) -> bool:
        """Apply every pending request, then report the list; True when a loop changed.

        A request already applied here is acknowledged with its first outcome
        and never applied again. One that cannot be decided safely (the ledger
        or the task store is unreadable) stays pending for the next pass.
        """
        with self._lock:
            return self._sync_locked()

    def _sync_locked(self) -> bool:
        changed = False
        for request in self._pending_requests():
            request_id = str(request.get("id") or "")
            try:
                outcome = self._ledger.get(self._organization, request_id)
            except LedgerUnavailable:
                logger.warning("[webapp-loops] request ledger unavailable; retrying later")
                break
            if outcome is None:
                start: Callable[[], None] | None = None
                if request.get("action") == _RUN_ACTION:
                    outcome, start = self._accept_run(request)
                else:
                    outcome = self._apply(request)
                    changed = changed or bool(outcome and outcome.applied)
                if outcome is None:
                    continue
                try:
                    self._ledger.record(self._organization, request_id, outcome)
                except LedgerUnavailable:
                    logger.warning("[webapp-loops] could not record request %s", request_id)
                    if start is not None:
                        # Unrecorded, a redelivery could run it again: leave it pending.
                        self._release_run(outcome.loop_id or "")
                        continue
                if start is not None:
                    start()
            self._acknowledge(request_id, outcome)
        if changed:
            request_scheduler_reload()
        self._report_locked(force=changed)
        return changed

    def report(self, *, force: bool = False) -> None:
        """Send the loop list when it changed, when forced, or when the heartbeat is due.

        Reading the local store is free; the web app is called only when the
        list differs from the last report or the heartbeat is due.
        """
        with self._lock:
            self._report_locked(force=force)

    def _report_locked(self, *, force: bool) -> None:
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

    def _accept_run(
        self, request: Mapping[str, Any]
    ) -> tuple[RecordedOutcome | None, Callable[[], None] | None]:
        """Reserve the loop for one run; the callable starts it once the outcome is recorded.

        ``(None, None)`` leaves the request pending: this gateway is shutting
        down, and the next one picks it up.
        """
        loop_id = str(request.get("loop_id") or "")
        actor = str(request.get("requested_by") or "").strip() or _WEBAPP_ACTOR
        runners = self._runners
        if runners is None:
            return RecordedOutcome(False, error=_RUN_UNAVAILABLE), None
        try:
            with self._scope(actor):
                summary, _error = resolve_loop_summary(loop_id) if loop_id else (None, "")
            if summary is None:
                return RecordedOutcome(False, error=_MISSING_LOOP), None
            with self._runs_lock:
                if self._closing:
                    return None, None
                # A scheduled tick of this loop in flight counts as a run in progress.
                if summary.id in self._running or has_live_claim(task_ids=summary.task_ids):
                    return RecordedOutcome(False, error=_RUN_IN_PROGRESS), None
                self._running.add(summary.id)
        except Exception:
            logger.exception("[webapp-loops] accepting run request %s failed", request.get("id"))
            return RecordedOutcome(False, error=_UNEXPECTED_FAILURE), None
        accepted, task_ids = summary.id, summary.task_ids
        return RecordedOutcome(True, loop_id=accepted), lambda: self._start_run(
            accepted, task_ids, runners
        )

    def _start_run(
        self, loop_id: str, task_ids: tuple[str, ...], runners: SchedulerRunners
    ) -> None:
        thread = threading.Thread(
            target=self._run_loop,
            args=(loop_id, task_ids, runners),
            name=f"opensre-webapp-loop-run-{loop_id}",
            daemon=True,
        )
        with self._runs_lock:
            self._run_threads.add(thread)
        try:
            thread.start()
        except RuntimeError:
            logger.exception("[webapp-loops] could not start a run of loop %s", loop_id)
            with self._runs_lock:
                self._run_threads.discard(thread)
            self._release_run(loop_id)

    def _run_loop(self, loop_id: str, task_ids: tuple[str, ...], runners: SchedulerRunners) -> None:
        """Fire every task of the loop once, like a scheduled tick, then report the list."""
        try:
            for task_id in task_ids:
                if not run_task_now(task_id, runners, run_paused=True):
                    logger.warning("[webapp-loops] run of loop %s task %s failed", loop_id, task_id)
        except Exception:
            logger.exception("[webapp-loops] run of loop %s failed", loop_id)
        finally:
            self._release_run(loop_id)
            try:
                self.report()
            except Exception:
                logger.exception("[webapp-loops] report after the run of loop %s failed", loop_id)
            with self._runs_lock:
                self._run_threads.discard(threading.current_thread())

    def _release_run(self, loop_id: str) -> None:
        with self._runs_lock:
            self._running.discard(loop_id)

    def drain_runs(self, timeout: float, *, clock: Callable[[], float] = time.monotonic) -> bool:
        """Accept no more runs and wait up to ``timeout`` for those running; True when all ended."""
        with self._runs_lock:
            self._closing = True
            threads = list(self._run_threads)
        deadline = clock() + max(0.0, timeout)
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - clock()))
        return not any(thread.is_alive() for thread in threads)

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
        # A one-shot work-item reminder runs at a time, not on a cron; the
        # Tasks page lists recurring schedules only.
        return [
            _loop_row(loop, [by_id[task_id] for task_id in loop.task_ids if task_id in by_id])
            for loop in summarize_loops(tasks)
            if loop.cron.strip()
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
            # The web app's own short error text, so a rejected report says why.
            logger.warning(
                "[webapp-loops] %s %s -> %s %s",
                method,
                path,
                response.status_code,
                _error_text(response),
            )
        return response


def webapp_loop_sync_from_environment(
    runners: SchedulerRunners | None = None,
) -> WebappLoopSync | None:
    """The sync for this hosted gateway, or ``None`` off a hosted organization.

    Needs the organization id, an https webapp URL, and the gateway token.
    ``runners`` are the scheduler's own, so a ``run`` request fires the loop as
    a tick would (and takes the same capacity gate).
    """
    org = organization_id()
    base_url = (os.getenv(WEBAPP_URL_ENV) or "").strip()
    token = resolve_account_token()
    if not org or not token or not base_url.lower().startswith("https://"):
        return None
    return WebappLoopSync(base_url=base_url, organization=org, token=token, runners=runners)


def _error_text(response: Any) -> str:
    """The ``error`` field of a web app error response, capped; "" when absent."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return ""
    return str(error)[:200] if error else ""


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
