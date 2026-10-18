"""The gateway half of the webapp Tasks-page contract.

Tasks created in the app stayed pending forever: no gateway pulled the
requests, and the Tasks page never listed the agent's loops. These pin the
at-least-once create, organization isolation, and a mirror that is never
shrunk by an unreadable store.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

import infrastructure.scheduling.scheduler as scheduler_pkg
import integrations.webapp_scheduled_loops.request_ledger as request_ledger
import integrations.webapp_scheduled_loops.sync as sync_module
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.scheduling.scheduler.loops import create_manual_loop
from infrastructure.scheduling.scheduler.storage import TaskStoreSnapshot, get_task, list_tasks
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
    monkeypatch.setattr(request_ledger, "OPENSRE_HOME_DIR", home)


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
        base_url="https://app.example", organization=_ORG, token="gateway-token", http=webapp
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
