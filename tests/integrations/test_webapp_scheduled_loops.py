"""The gateway half of the webapp Tasks-page contract.

Tasks created in the app stayed pending forever: no gateway pulled the
requests, and the Tasks page never listed the agent's loops. These pin the
at-least-once create, organization isolation, and a mirror that is never
shrunk by an unreadable store.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import sqlite3
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

import infrastructure.scheduling.scheduler as scheduler_pkg
import infrastructure.scheduling.scheduler.storage.database as scheduler_database
import integrations.webapp_requests.ledger as request_ledger
import integrations.webapp_scheduled_loops.loop_runs as loop_runs_module
import integrations.webapp_scheduled_loops.sync as sync_module
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_GROUP_ID_PARAM,
    LOOP_PROMPT_PARAM,
)
from infrastructure.scheduling.scheduler.loops import create_manual_loop
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.storage import (
    TaskStoreSnapshot,
    add_task,
    get_task,
    list_tasks,
    record_task_success,
)
from infrastructure.scheduling.scheduler.types import (
    DeliveryOutcome,
    Provider,
    ScheduledTask,
    TaskKind,
    TaskStatus,
)
from integrations.webapp_scheduled_loops import WebappLoopSync

_ORG = "org_A"
_REQUEST_ID = "6c1d2e3f-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _scheduler_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every scheduler module that holds the home directory at ``tmp_path``."""
    home = tmp_path / "opensre-home"
    for info in pkgutil.walk_packages(scheduler_pkg.__path__, f"{scheduler_pkg.__name__}."):
        module = importlib.import_module(info.name)
        if hasattr(module, "OPENSRE_HOME_DIR"):
            monkeypatch.setattr(module, "OPENSRE_HOME_DIR", home)


@dataclass
class _Response:
    status_code: int
    body: dict[str, Any] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        return self.body


@dataclass
class _Webapp:
    """Stands in for the webapp routes: queued requests in, acks and reports out."""

    pending: list[dict[str, Any]] = field(default_factory=list)
    acks: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    reports: list[list[dict[str, Any]]] = field(default_factory=list)
    totals: list[int] = field(default_factory=list)

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        assert kwargs["headers"]["Authorization"] == "Bearer gateway-token"
        if method == "GET":
            return _Response(HTTPStatus.OK, {"success": True, "data": list(self.pending)})
        if method == "POST":
            self.acks.append((url.rsplit("/", 1)[1], kwargs["json"]))
            return _Response(HTTPStatus.OK)
        self.reports.append(kwargs["json"]["loops"])
        self.totals.append(kwargs["json"]["total"])
        return _Response(HTTPStatus.OK)


def _sync(webapp: _Webapp) -> WebappLoopSync:
    return WebappLoopSync(
        base_url="https://app.example",
        organization=_ORG,
        token="gateway-token",
        http=webapp,
        runners=SchedulerRunners(agent=lambda _payload: ""),
    )


def _create_request() -> dict[str, Any]:
    return {
        "id": _REQUEST_ID,
        "action": "create",
        "loop_id": None,
        "payload": {
            "name": "Failed branches",
            "prompt": "List branches in Tracer-Cloud/opensre whose latest CI run failed.",
            "cron": "0 9 * * 1-5",
            "timezone": "Europe/London",
            "channels": [],
            "mode": "report",
        },
        "requested_by": "user_2abc",
    }


def test_a_task_created_in_the_app_is_scheduled_once_and_listed_back() -> None:
    # Arrange: the app queued a create; the ack was lost, so it is delivered twice.
    webapp = _Webapp(pending=[_create_request()])
    sync = _sync(webapp)

    # Act
    sync.sync()
    sync.sync()

    # Assert: one loop, owned by the org and the member, acked both times with its id.
    [task] = list_tasks()
    assert task.organization == _ORG
    assert task.params["loop_created_by"] == "user_2abc"
    assert [ack["status"] for _, ack in webapp.acks] == ["applied", "applied"]
    assert {ack["loopId"] for _, ack in webapp.acks} == {task.id}
    assert webapp.reports[0][0]["id"] == task.id
    assert webapp.reports[0][0]["webapp_request_id"] == _REQUEST_ID


def test_the_app_cannot_pause_another_organizations_loop() -> None:
    # Arrange: org B's loop in the shared store, and org A's app names it.
    with bound_storage_scope(StorageScope(principal=Principal.org("org_B"), actor=Actor(id="u"))):
        other = create_manual_loop(name="B", prompt="Org B report.", cron="0 8 * * *")
    webapp = _Webapp(pending=[{"id": _REQUEST_ID, "action": "pause", "loop_id": other.task.id}])

    # Act
    _sync(webapp).sync()

    # Assert: refused, and org B's loop keeps running and is not reported to org A.
    assert webapp.acks[0][1]["status"] == "failed"
    stored = get_task(other.task.id)
    assert stored is not None and stored.enabled
    assert webapp.reports == [[]]


def test_an_unreadable_store_never_empties_the_apps_task_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the store read is incomplete (a damaged row, a mount hiccup).
    def partial() -> TaskStoreSnapshot:
        return TaskStoreSnapshot(tasks=(), complete=False, missing=False)

    monkeypatch.setattr(sync_module, "get_task_store_snapshot", partial)
    webapp = _Webapp()

    # Act
    _sync(webapp).report(force=True)

    # Assert: nothing is sent, so the mirror keeps its last good list.
    assert webapp.reports == []


def test_a_time_changed_in_the_app_reschedules_the_same_loop() -> None:
    # Arrange: a task created in the app, then edited there.
    webapp = _Webapp(pending=[_create_request()])
    sync = _sync(webapp)
    sync.sync()
    [created] = list_tasks()
    webapp.pending = [
        {
            "id": "6c1d2e3f-0000-4000-8000-000000000002",
            "action": "update",
            "loop_id": created.id,
            "payload": {"cron": "30 7 * * 1-5", "timezone": "America/New_York"},
            "requested_by": "user_2abc",
        }
    ]

    # Act
    sync.sync()

    # Assert: one loop, rescheduled, and the app hears the new time.
    [task] = list_tasks()
    assert task.id == created.id
    assert (task.cron, task.timezone) == ("30 7 * * 1-5", "America/New_York")
    assert webapp.acks[-1][1] == {
        "organizationId": _ORG,
        "status": "applied",
        "loopId": created.id,
    }
    assert webapp.reports[-1][0]["cron"] == "30 7 * * 1-5"


def test_a_redelivered_pause_never_undoes_a_later_resume() -> None:
    # Arrange: the app paused one of the organization's loops; the ack was lost.
    scope = StorageScope(principal=Principal.org(_ORG), actor=Actor(id="u"))
    with bound_storage_scope(scope):
        created = create_manual_loop(name="Daily", prompt="Daily report.", cron="0 9 * * *")
    pause = {
        "id": "6c1d2e3f-0000-4000-8000-000000000003",
        "action": "pause",
        "loop_id": created.task.id,
        "requested_by": "user_2abc",
    }
    webapp = _Webapp(pending=[pause])
    sync = _sync(webapp)
    sync.sync()
    # A member then resumes it from Slack.
    with bound_storage_scope(scope):
        sync_module.set_loop_enabled(created.task.id, enabled=True)

    # Act: the app redelivers the unacknowledged pause.
    sync.sync()

    # Assert: re-acknowledged with its first outcome, and the loop stays resumed.
    stored = get_task(created.task.id)
    assert stored is not None and stored.enabled
    assert [ack["status"] for _, ack in webapp.acks] == ["applied", "applied"]


def test_a_create_recorded_before_the_ledger_was_shared_is_not_created_again() -> None:
    # The shared ledger names the produced id ``ref``; a ledger already on a
    # gateway's volume calls it ``loop_id`` and must still answer redeliveries.
    import json
    import time

    from config.constants import paths

    ledger = paths.OPENSRE_HOME_DIR / "webapp_loop_requests.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps(
            {
                f"{_ORG}:{_REQUEST_ID}": {
                    "applied": True,
                    "loop_id": "loop_first",
                    "error": "",
                    "recorded_at": time.time(),
                }
            }
        ),
        encoding="utf-8",
    )
    webapp = _Webapp(pending=[_create_request()])

    _sync(webapp).sync()

    assert list_tasks() == []
    assert webapp.acks == [
        (_REQUEST_ID, {"organizationId": _ORG, "status": "applied", "loopId": "loop_first"})
    ]


def test_a_create_waits_while_the_store_cannot_be_read_completely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: an unreadable row might be the loop this request already created.
    def partial() -> TaskStoreSnapshot:
        return TaskStoreSnapshot(tasks=(), complete=False, missing=False)

    monkeypatch.setattr(sync_module, "get_task_store_snapshot", partial)
    webapp = _Webapp(pending=[_create_request()])

    # Act
    _sync(webapp).sync()

    # Assert: nothing created and nothing acknowledged, so the app redelivers it later.
    assert list_tasks() == []
    assert webapp.acks == []


def test_a_list_over_the_report_cap_says_it_is_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: more loops than one report may carry.
    monkeypatch.setattr(sync_module, "_MAX_REPORTED_LOOPS", 1)
    for name in ("First", "Second"):
        with bound_storage_scope(StorageScope(principal=Principal.org(_ORG), actor=Actor(id="u"))):
            create_manual_loop(name=name, prompt=f"{name} report.", cron="0 9 * * *")
    webapp = _Webapp()

    # Act
    _sync(webapp).report(force=True)

    # Assert: the cap is applied and the full count travels with it.
    assert len(webapp.reports[0]) == 1
    assert webapp.totals == [2]


def test_a_one_shot_reminder_never_reaches_the_tasks_page() -> None:
    """The Tracer store's old reminder has no cron; the web app refused every report for it."""
    # Arrange: a recurring loop and a one-shot work-item reminder in the same store.
    scope = StorageScope(principal=Principal.org(_ORG), actor=Actor(id="u"))
    with bound_storage_scope(scope):
        loop = create_manual_loop(name="Daily", prompt="Daily report.", cron="0 9 * * *")
        add_task(
            ScheduledTask(
                kind=TaskKind.WORK_ITEM_REMINDER,
                cron="",
                provider=Provider.SLACK,
                enabled=False,
                params={"run_at": "2026-10-06T22:18:01+00:00", "work_item_id": "b82ef1fa42a8"},
            )
        )
    webapp = _Webapp()

    # Act
    _sync(webapp).report(force=True)

    # Assert: only the recurring loop is reported, and every row has a cron.
    assert [row["id"] for row in webapp.reports[0]] == [loop.task.id]


def test_a_run_now_fires_a_paused_loop_once_in_the_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redelivered run must not run again, and a second run waits for the first."""
    # Arrange: a paused loop, and a run that holds until released.
    scope = StorageScope(principal=Principal.org(_ORG), actor=Actor(id="u"))
    with bound_storage_scope(scope):
        created = create_manual_loop(name="Daily", prompt="Daily report.", cron="0 9 * * *")
        sync_module.set_loop_enabled(created.task.id, enabled=False)
    started, release = threading.Event(), threading.Event()
    runs: list[tuple[str, bool]] = []

    def run_task_now(task_id: str, _runners: SchedulerRunners, *, run_paused: bool) -> bool:
        runs.append((task_id, run_paused))
        started.set()
        release.wait(timeout=10)
        return record_task_success(task_id)

    monkeypatch.setattr(sync_module, "run_task_now", run_task_now)
    first = {"id": _REQUEST_ID, "action": "run", "loop_id": created.task.id, "payload": {}}
    second = {**first, "id": "6c1d2e3f-0000-4000-8000-000000000009"}
    webapp = _Webapp(pending=[first])
    sync = _sync(webapp)

    try:
        # Act: the poll returns while the run is still going; the first ack was lost.
        sync.sync()
        assert started.wait(timeout=10)
        webapp.pending = [first, second]
        sync.sync()
        release.set()
        for thread in threading.enumerate():
            if thread.name.startswith("opensre-webapp-loop-run-"):
                thread.join(timeout=10)
    finally:
        release.set()

    # Assert: one run of the paused loop, the redelivery re-acked, the overlap refused.
    assert runs == [(created.task.id, True)]
    assert [(request_id, ack["status"]) for request_id, ack in webapp.acks] == [
        (first["id"], "applied"),
        (first["id"], "applied"),
        (second["id"], "failed"),
    ]
    stored = get_task(created.task.id)
    assert stored is not None and not stored.enabled
    assert webapp.reports[-1][0]["last_run"] == stored.last_run


def _loop_run_request(request_id: str = _REQUEST_ID) -> tuple[str, dict[str, Any]]:
    scope = StorageScope(principal=Principal.org(_ORG), actor=Actor(id="u"))
    with bound_storage_scope(scope):
        created = create_manual_loop(name="Daily", prompt="Daily report.", cron="0 9 * * *")
    request = {"id": request_id, "action": "run", "loop_id": created.task.id, "payload": {}}
    return created.task.id, request


def _join_runs() -> None:
    for thread in threading.enumerate():
        if thread.name.startswith("opensre-webapp-loop-run-"):
            thread.join(timeout=10)


def test_a_run_now_that_cannot_be_recorded_stays_pending_and_runs_once_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrecorded, a redelivered run could run twice; so it must not start at all."""
    # Arrange: the ledger refuses the first write, then works.
    loop_id, request = _loop_run_request()
    runs: list[str] = []

    def run_task_now(task_id: str, _runners: SchedulerRunners, *, run_paused: bool) -> bool:
        runs.append(task_id)
        return True

    monkeypatch.setattr(sync_module, "run_task_now", run_task_now)
    real_record = request_ledger.RequestLedger.record
    failures = iter([True])

    def record(self: request_ledger.RequestLedger, *args: Any) -> None:
        if next(failures, False):
            raise request_ledger.LedgerUnavailable("disk full")
        real_record(self, *args)

    monkeypatch.setattr(request_ledger.RequestLedger, "record", record)
    webapp = _Webapp(pending=[request])
    sync = _sync(webapp)

    # Act: the first pass cannot record it; the second can.
    sync.sync()
    _join_runs()
    runs_after_failed_record = list(runs)
    acks_after_failed_record = list(webapp.acks)
    sync.sync()
    _join_runs()

    # Assert: nothing ran or was acked until it was recorded, then one run.
    assert runs_after_failed_record == []
    assert acks_after_failed_record == []
    assert runs == [loop_id]
    assert [ack["status"] for _, ack in webapp.acks] == ["applied"]


def test_shutdown_waits_for_a_run_now_and_leaves_later_ones_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a run that holds until released.
    _, first = _loop_run_request()
    started, release = threading.Event(), threading.Event()

    def run_task_now(task_id: str, _runners: SchedulerRunners, *, run_paused: bool) -> bool:
        started.set()
        return release.wait(timeout=10)

    monkeypatch.setattr(sync_module, "run_task_now", run_task_now)
    webapp = _Webapp(pending=[first])
    sync = _sync(webapp)
    sync.sync()
    assert started.wait(timeout=10)

    try:
        # Act: shutdown gives up while it runs, then waits for it to finish.
        cut_short = sync.drain_runs(0.05)
        release.set()
        finished = sync.drain_runs(10)
        webapp.pending = [{**first, "id": "6c1d2e3f-0000-4000-8000-00000000000a"}]
        webapp.acks.clear()
        sync.sync()
    finally:
        release.set()

    # Assert: the run outlived a short drain, ended within a long one, and a run
    # asked for during shutdown is neither run nor acked (the next gateway takes it).
    assert (cut_short, finished) == (False, True)
    assert webapp.acks == []


def _update(loop_id: str, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": request_id,
        "action": "update",
        "loop_id": loop_id,
        "payload": {"name": "Failed branches", "prompt": "List failed branches.", **payload},
        "requested_by": "user_2abc",
    }


def test_the_app_can_change_where_a_loop_delivers() -> None:
    # Arrange: a loop created in the app.
    webapp = _Webapp(pending=[_create_request()])
    sync = _sync(webapp)
    sync.sync()
    [created] = list_tasks()

    # Act: add email, then take it away again.
    webapp.pending = [
        _update(
            created.id,
            "6c1d2e3f-0000-4000-8000-0000000000e1",
            {
                "channels": ["interactive_shell", "email"],
                "email_to": "ana@example.com, ben@example.com",
                "email_account": "gmail:apn_1",
            },
        )
    ]
    sync.sync()
    [with_email] = list_tasks()
    webapp.pending = [
        _update(
            created.id, "6c1d2e3f-0000-4000-8000-0000000000e2", {"channels": ["interactive_shell"]}
        )
    ]
    sync.sync()
    [without_email] = list_tasks()

    # Assert: same loop throughout; email destination set, then cleared.
    assert with_email.id == without_email.id == created.id
    assert with_email.params["loop_channels"] == "interactive_shell,email"
    assert with_email.params["loop_email_to"] == "ana@example.com,ben@example.com"
    assert with_email.params["loop_email_account"] == "gmail:apn_1"
    assert without_email.params["loop_channels"] == "interactive_shell"
    assert "loop_email_to" not in without_email.params
    assert [ack["status"] for _, ack in webapp.acks[-2:]] == ["applied", "applied"]


def test_a_delivery_edit_with_a_bad_address_is_refused_and_changes_nothing() -> None:
    # Arrange
    webapp = _Webapp(pending=[_create_request()])
    sync = _sync(webapp)
    sync.sync()
    [created] = list_tasks()
    webapp.pending = [
        _update(
            created.id,
            "6c1d2e3f-0000-4000-8000-0000000000e3",
            {"channels": ["email"], "email_to": "not-an-address", "email_account": "smtp:r1"},
        )
    ]

    # Act
    sync.sync()

    # Assert
    [task] = list_tasks()
    assert webapp.acks[-1][1]["status"] == "failed"
    assert "not an email address" in webapp.acks[-1][1]["error"]
    assert task.params["loop_channels"] == created.params["loop_channels"]


def test_an_edit_may_change_only_where_a_loop_delivers() -> None:
    # Arrange: an update with delivery and nothing else.
    webapp = _Webapp(pending=[_create_request()])
    sync = _sync(webapp)
    sync.sync()
    [created] = list_tasks()
    webapp.pending = [
        {
            "id": "6c1d2e3f-0000-4000-8000-0000000000e4",
            "action": "update",
            "loop_id": created.id,
            "payload": {
                "channels": ["email"],
                "email_to": "ana@example.com",
                "email_account": "smtp:r1",
            },
            "requested_by": "user_2abc",
        }
    ]

    # Act
    sync.sync()

    # Assert: applied, with name and instructions untouched.
    [task] = list_tasks()
    assert webapp.acks[-1][1]["status"] == "applied"
    assert task.params["loop_channels"] == "email"
    assert (task.name, task.params["loop_prompt"]) == (
        created.name,
        created.params["loop_prompt"],
    )


def test_an_empty_channel_list_is_refused_not_ignored() -> None:
    # Arrange
    webapp = _Webapp(pending=[_create_request()])
    sync = _sync(webapp)
    sync.sync()
    [created] = list_tasks()
    webapp.pending = [_update(created.id, "6c1d2e3f-0000-4000-8000-0000000000e5", {"channels": []})]

    # Act
    sync.sync()

    # Assert
    [task] = list_tasks()
    assert webapp.acks[-1][1]["status"] == "failed"
    assert "at least one place" in webapp.acks[-1][1]["error"]
    assert task.params["loop_channels"] == created.params["loop_channels"]


# Runs: each loop's recent attempts feed the activity timeline on the loop's page.


def _org_scope() -> Any:
    return bound_storage_scope(StorageScope(principal=Principal.org(_ORG), actor=Actor(id="u")))


def _record_run(
    task_id: str,
    started_at: str,
    *,
    fire_time: str = "",
    status: TaskStatus = TaskStatus.SUCCESS,
    finished: bool = True,
    error: str = "",
    summary: str = "",
    targets: tuple[DeliveryOutcome, ...] = (),
) -> int:
    """Insert one attempt as the scheduler stores it; returns its run id."""
    with scheduler_database.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO task_runs (task_id, fire_time, started_at, finished_at, status, "
            "error, report_summary, targets) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                fire_time or started_at,
                started_at,
                started_at.replace("00Z", "42Z") if finished else None,
                status.value,
                error,
                summary,
                json.dumps([target.model_dump(mode="json") for target in targets]),
            ),
        )
        return int(cursor.lastrowid or 0)


def _grouped_task(task_id: str, chat_id: str) -> ScheduledTask:
    """One destination of a loop delivered to several places."""
    return ScheduledTask(
        id=task_id,
        name="Grouped",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * *",
        provider=Provider.SLACK,
        chat_id=chat_id,
        params={LOOP_GROUP_ID_PARAM: "group-a", LOOP_PROMPT_PARAM: "Grouped report."},
    )


def test_each_loop_reports_its_newest_ten_runs_across_its_tasks() -> None:
    # Arrange: a loop of two tasks with 12 runs between them, and a loop that never ran.
    with _org_scope():
        add_task(_grouped_task("task-a", "C1"))
        add_task(_grouped_task("task-b", "C2"))
        idle = create_manual_loop(name="Idle", prompt="Idle report.", cron="0 8 * * *")
    for hour in range(12):
        _record_run("task-a" if hour % 2 else "task-b", f"2026-10-18T{hour:02d}:00:00Z")
    webapp = _Webapp()

    # Act
    _sync(webapp).report(force=True)

    # Assert: newest first, capped at 10, interleaved across the tasks; [] when never run.
    rows = {row["id"]: row for row in webapp.reports[0]}
    started = [run["started_at"] for run in rows["group-a"]["runs"]]
    assert started == [f"2026-10-18T{hour:02d}:00:00Z" for hour in range(11, 1, -1)]
    assert rows[idle.task.id]["runs"] == []


def test_a_run_reports_its_trigger_and_only_the_outcome_it_has() -> None:
    # Arrange: a scheduled tick delivered to one of two places; a Run now still running.
    with _org_scope():
        loop = create_manual_loop(name="Daily", prompt="Daily report.", cron="0 9 * * *")
    task_id = loop.task.id
    tick = _record_run(
        task_id,
        "2026-10-18T09:00:00Z",
        fire_time="2026-10-18T09:00:00+00:00",
        targets=(
            DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=True),
            DeliveryOutcome(provider=Provider.TELEGRAM, chat_id="T1", ok=False),
        ),
    )
    manual = _record_run(
        task_id,
        "2026-10-18T10:00:00Z",
        fire_time="2026-10-18T10:00:00.123456+00:00",
        status=TaskStatus.RUNNING,
        finished=False,
    )
    webapp = _Webapp()

    # Act
    _sync(webapp).report(force=True)

    # Assert
    running, ticked = webapp.reports[0][0]["runs"]
    assert running == {
        "id": f"{task_id}:{manual}",
        "trigger": "manual",
        "status": "running",
        "started_at": "2026-10-18T10:00:00Z",
        "error": "",
        "summary": "",
        "attempt": 1,
    }
    assert ticked["id"] == f"{task_id}:{tick}"
    assert (ticked["trigger"], ticked["delivery"]) == ("schedule", "partial")
    assert ticked["finished_at"] == "2026-10-18T09:00:42Z"


def test_run_text_is_redacted_and_capped() -> None:
    # Arrange: a failure that echoes a token, and an oversized summary.
    with _org_scope():
        loop = create_manual_loop(name="Daily", prompt="Daily report.", cron="0 9 * * *")
    token = "ghp_" + "a" * 36
    _record_run(
        loop.task.id,
        "2026-10-18T09:00:00Z",
        status=TaskStatus.FAILED,
        error=f"GitHub refused {token} " + "x" * 2_000,
        summary="é" * 2_000,
    )
    webapp = _Webapp()

    # Act
    _sync(webapp).report(force=True)

    # Assert: no credential leaves, and both fit the contract's caps.
    [run] = webapp.reports[0][0]["runs"]
    assert token not in run["error"] and "[REDACTED:github_pat]" in run["error"]
    assert len(run["error"]) <= 600 and run["error"].endswith("…")
    assert len(run["summary"].encode()) <= 1_000 and run["summary"].endswith("…")


def test_a_run_ending_is_reported_and_an_unchanged_one_is_not() -> None:
    # Arrange: reported while the run was in progress.
    with _org_scope():
        loop = create_manual_loop(name="Daily", prompt="Daily report.", cron="0 9 * * *")
    run_id = _record_run(
        loop.task.id, "2026-10-18T09:00:00Z", status=TaskStatus.RUNNING, finished=False
    )
    webapp = _Webapp()
    sync = _sync(webapp)
    sync.report()

    # Act: the run ends, then nothing changes.
    with scheduler_database.transaction() as conn:
        conn.execute(
            "UPDATE task_runs SET status = ?, finished_at = ? WHERE id = ?",
            (TaskStatus.SUCCESS.value, "2026-10-18T09:01:00Z", run_id),
        )
    sync.report()
    sync.report()

    # Assert: the same run id is resent once with its new status.
    assert [report[0]["runs"][0]["status"] for report in webapp.reports] == ["running", "success"]
    assert webapp.reports[0][0]["runs"][0]["id"] == webapp.reports[1][0]["runs"][0]["id"]


def test_an_unreadable_run_history_still_reports_the_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the run database cannot be read.
    def unreadable(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(loop_runs_module, "get_recent_runs", unreadable)
    with _org_scope():
        loop = create_manual_loop(name="Daily", prompt="Daily report.", cron="0 9 * * *")
    webapp = _Webapp()

    # Act
    _sync(webapp).report(force=True)

    # Assert: the loop is listed, and ``runs`` is left out rather than sent empty.
    [row] = webapp.reports[0]
    assert row["id"] == loop.task.id
    assert "runs" not in row


def test_a_report_too_large_for_one_request_sends_fewer_newest_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: ten runs with long summaries, against a small body limit.
    monkeypatch.setattr(sync_module, "_MAX_REPORT_BYTES", 3_000)
    with _org_scope():
        loop = create_manual_loop(name="Daily", prompt="Daily report.", cron="0 9 * * *")
    for hour in range(10):
        _record_run(loop.task.id, f"2026-10-18T{hour:02d}:00:00Z", summary="s" * 500)
    webapp = _Webapp()

    # Act
    _sync(webapp).report(force=True)

    # Assert: the loop still goes out, with its newest runs, inside the limit.
    [row] = webapp.reports[0]
    started = [run["started_at"] for run in row["runs"]]
    assert 0 < len(started) < 10
    assert started == [f"2026-10-18T{hour:02d}:00:00Z" for hour in range(9, 9 - len(started), -1)]
    assert len(json.dumps(webapp.reports[0]).encode()) <= 3_000
