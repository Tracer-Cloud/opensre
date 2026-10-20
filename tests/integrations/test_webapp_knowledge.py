"""The gateway half of the Knowledge page: one member's memories stay theirs.

The mirror carries every member's memories to the web app, and a member can
forget one from the page. These pin that a forget touches only the asking
member's store, that a redelivered request is acknowledged again without
being applied again, that an unchanged mirror is not resent until the
heartbeat, and that an older web app without the routes is harmless.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.domain.knowledge import save_workspace_entry
from core.domain.memory import MemoryType, list_memories, save_memory
from integrations.webapp_knowledge import WebappKnowledgeSync
from integrations.webapp_requests import RequestLedger

_ORG = "org_A"


@pytest.fixture(autouse=True)
def _org_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-member stores under the org root, as on a hosted gateway."""
    monkeypatch.delenv(OPENSRE_MEMORY_DIR_ENV, raising=False)
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)


def _as_member(actor: str) -> Any:
    return bound_storage_scope(StorageScope(principal=Principal.org(_ORG), actor=Actor(actor)))


def _remember(actor: str, slug: str, description: str) -> None:
    with _as_member(actor):
        save_memory(slug=slug, memory_type="preference", description=description, body=description)


def _memories(actor: str) -> list[str]:
    with _as_member(actor):
        return [record.slug for record in list_memories()]


@dataclass
class _Response:
    status_code: int
    body: dict[str, Any] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        return self.body


@dataclass
class _Webapp:
    pending: list[dict[str, Any]] = field(default_factory=list)
    acks: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    reports: list[dict[str, Any]] = field(default_factory=list)
    routes: bool = True

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        assert kwargs["headers"]["Authorization"] == "Bearer gateway-token"
        if not self.routes:
            return _Response(HTTPStatus.NOT_FOUND)
        if method == "GET":
            return _Response(HTTPStatus.OK, {"success": True, "data": list(self.pending)})
        if method == "POST":
            self.acks.append((url.rsplit("/", 1)[1], kwargs["json"]))
            return _Response(HTTPStatus.OK)
        self.reports.append(kwargs["json"])
        return _Response(HTTPStatus.OK)


def _sync(webapp: _Webapp, tmp_path: Path, **kwargs: Any) -> WebappKnowledgeSync:
    clock = kwargs.pop("clock", lambda: 0.0)
    return WebappKnowledgeSync(
        base_url="https://app.example",
        organization=_ORG,
        token="gateway-token",
        http=webapp,
        clock=clock,
        ledger=RequestLedger(tmp_path / "ledger.json"),
        **kwargs,
    )


def test_a_forget_from_the_page_touches_only_the_asking_members_memory(tmp_path: Path) -> None:
    _remember("U_ADA", "concise-reports", "Ada wants concise reports")
    _remember("U_GRACE", "concise-reports", "Grace wants concise reports")
    request = {
        "id": "11111111-0000-4000-8000-000000000001",
        "action": "forget_memory",
        "actor": "U_ADA",
        "payload": {"name": "concise-reports"},
    }
    webapp = _Webapp(pending=[request])
    sync = _sync(webapp, tmp_path)

    sync.sync()
    # The ack was lost: the same request is delivered again.
    _remember("U_ADA", "concise-reports", "Ada said it again after the forget")
    sync.sync()

    assert [ack for _id, ack in webapp.acks] == [
        {"organizationId": _ORG, "status": "applied"},
        {"organizationId": _ORG, "status": "applied"},
    ]
    # Applied once: the memory Ada saved after the forget survives the redelivery.
    assert _memories("U_ADA") == ["concise-reports"]
    assert _memories("U_GRACE") == ["concise-reports"]


def test_the_mirror_files_each_memory_under_its_member_and_is_not_resent_unchanged(
    tmp_path: Path,
) -> None:
    _remember("U_ADA", "concise-reports", "Ada wants concise reports")
    _remember("U_GRACE", "tables-please", "Grace wants tables")
    save_workspace_entry(
        slug="organization",
        memory_type=MemoryType.ORGANIZATION,
        description="Acme profile",
        body="# Acme",
    )
    clock = {"now": 0.0}
    webapp = _Webapp()
    sync = _sync(webapp, tmp_path, clock=lambda: clock["now"])

    sync.sync()
    sync.sync()

    assert len(webapp.reports) == 1
    report = webapp.reports[0]
    members = {member["actor"]: member for member in report["members"]}
    assert [m["name"] for m in members["U_ADA"]["memories"]] == ["concise-reports"]
    assert [m["name"] for m in members["U_GRACE"]["memories"]] == ["tables-please"]
    assert members["U_ADA"]["audit"]["total"] == 1
    assert [entry["name"] for entry in report["workspace"]] == ["organization"]
    assert report["audit"]["members"] == 2

    clock["now"] = 11 * 60
    sync.sync()
    assert len(webapp.reports) == 2  # the heartbeat resends an unchanged mirror


class _ScriptedStop(threading.Event):
    """The sync loop's stop event: each wait runs the next step, then the loop stops."""

    def __init__(self, *steps: Callable[[], None]) -> None:
        super().__init__()
        self._steps = list(steps)

    def wait(self, timeout: float | None = None) -> bool:  # noqa: ARG002 - a step stands in for the wait
        if self._steps:
            self._steps.pop(0)()
        else:
            self.set()
        return self.is_set()


def test_a_memory_saved_between_passes_reaches_the_page_before_the_next_pass(
    tmp_path: Path,
) -> None:
    # It used to wait for the next five-minute pass: a member who told the agent
    # something in Slack did not see it on the Knowledge page for minutes.
    webapp = _Webapp()
    sync = _sync(webapp, tmp_path)  # the clock never reaches the next full pass

    sync.run_forever(
        _ScriptedStop(
            lambda: _remember("U_ADA", "team-roles", "Ada is head of engineering"),
            lambda: None,  # nothing new: no store is read, nothing is sent
        )
    )

    assert len(webapp.reports) == 2
    first, second = webapp.reports
    assert first["members"] == []
    assert [m["name"] for m in second["members"][0]["memories"]] == ["team-roles"]


def test_a_rescan_reaches_the_scan_service_and_an_unknown_one_fails_politely(
    tmp_path: Path,
) -> None:
    asked: list[str] = []
    webapp = _Webapp(
        pending=[
            {
                "id": "22222222-0000-4000-8000-000000000001",
                "action": "rescan",
                "payload": {"service": "slack"},
            },
            {
                "id": "22222222-0000-4000-8000-000000000002",
                "action": "rescan",
                "payload": {"service": "jira"},
            },
        ]
    )

    def request_rescan(service: str) -> bool:
        asked.append(service)
        return service == "slack"

    sync = _sync(webapp, tmp_path, request_rescan=request_rescan)

    sync.sync()

    assert asked == ["slack", "jira"]
    statuses = [ack["status"] for _id, ack in webapp.acks]
    assert statuses == ["applied", "failed"]
    assert "jira" in webapp.acks[1][1]["error"]


def test_an_older_web_app_without_the_routes_is_harmless(tmp_path: Path) -> None:
    _remember("U_ADA", "concise-reports", "Ada wants concise reports")
    webapp = _Webapp(routes=False)

    changed = _sync(webapp, tmp_path).sync()

    assert changed is False
    assert webapp.acks == [] and webapp.reports == []
    assert _memories("U_ADA") == ["concise-reports"]


def _forget_request(request_id: str, actor: str = "U_ADA") -> dict[str, Any]:
    return {
        "id": request_id,
        "action": "forget_memory",
        "actor": actor,
        "payload": {"name": "concise-reports"},
    }


def test_a_forget_the_store_refused_stays_pending_instead_of_reading_as_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review finding: a delete that failed (lock timeout, disk error) was
    # acknowledged as applied, so the page said "forgotten" while it stayed.
    import integrations.webapp_knowledge.sync as sync_module

    _remember("U_ADA", "concise-reports", "Ada wants concise reports")
    webapp = _Webapp(pending=[_forget_request("33333333-0000-4000-8000-000000000001")])
    sync = _sync(webapp, tmp_path)
    real_delete = sync_module.delete_memory
    refusals = {"left": 1}

    def refuse_once(slug: str) -> bool:
        if refusals["left"]:
            refusals["left"] -= 1
            return False
        return real_delete(slug)

    monkeypatch.setattr(sync_module, "delete_memory", refuse_once)

    sync.sync()

    assert webapp.acks == []
    assert _memories("U_ADA") == ["concise-reports"]

    sync.sync()

    assert [ack["status"] for _id, ack in webapp.acks] == ["applied"]
    assert _memories("U_ADA") == []


def test_an_outcome_the_ledger_cannot_take_is_held_not_acknowledged_or_reapplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review finding: the outcome was acknowledged even when the ledger write
    # failed; if the ack was lost too, the redelivery deleted again.
    from integrations.webapp_requests import LedgerUnavailable

    _remember("U_ADA", "concise-reports", "Ada wants concise reports")
    webapp = _Webapp(pending=[_forget_request("44444444-0000-4000-8000-000000000001")])
    ledger = RequestLedger(tmp_path / "ledger.json")
    sync = WebappKnowledgeSync(
        base_url="https://app.example",
        organization=_ORG,
        token="gateway-token",
        http=webapp,
        clock=lambda: 0.0,
        ledger=ledger,
    )
    real_record = RequestLedger.record

    def failing_record(self: RequestLedger, *args: Any) -> None:
        raise LedgerUnavailable("disk full")

    monkeypatch.setattr(RequestLedger, "record", failing_record)
    sync.sync()
    assert webapp.acks == []

    # Ada saves the fact again; the redelivered forget must not delete it.
    _remember("U_ADA", "concise-reports", "Ada said it again")
    sync.sync()
    assert webapp.acks == []
    assert _memories("U_ADA") == ["concise-reports"]

    monkeypatch.setattr(RequestLedger, "record", real_record)
    sync.sync()
    assert [ack["status"] for _id, ack in webapp.acks] == ["applied"]
    assert _memories("U_ADA") == ["concise-reports"]


def test_a_large_organization_still_fits_one_report() -> None:
    # Review finding: nothing capped the report, and the web app's request
    # body limit would reject every report from a large organization.
    from core.domain.memory import MemberStore
    from core.domain.memory.models import MemoryRecord
    from integrations.webapp_knowledge.snapshot import build_snapshot

    def store(actor: str) -> MemberStore:
        records = tuple(
            MemoryRecord(
                slug=f"fact-{i}",
                memory_type=MemoryType.INFRASTRUCTURE,
                description=f"Fact {i}",
                created_at="2026-10-01T00:00:00+00:00",
                updated_at="2026-10-01T00:00:00+00:00",
                body="x" * 4_000,
            )
            for i in range(50)
        )
        return MemberStore(actor=actor, directory=Path("/unused"), records=records, usage={})

    report = build_snapshot(_ORG, stores=[store(f"U{i}") for i in range(20)], max_bytes=200_000)

    import json

    assert len(json.dumps(report, separators=(",", ":")).encode()) <= 200_000
    member = report["members"][0]
    assert member["total"] == 50 and member["audit"]["total"] == 50
    assert member["memories"] and member["memories"][0]["truncated"] is True


def test_the_report_fits_even_when_counts_alone_are_too_big() -> None:
    # Review finding: the shrinking steps could all fail and the oversized
    # report was sent anyway, so every update for the org was rejected.
    import json

    from core.domain.memory import MemberStore
    from integrations.webapp_knowledge.snapshot import build_snapshot

    stores = [
        MemberStore(actor=f"U{i:04d}", directory=Path("/unused"), records=(), usage={})
        for i in range(300)
    ]

    report = build_snapshot(_ORG, stores=stores, max_bytes=4_000)

    assert len(json.dumps(report, separators=(",", ":")).encode()) <= 4_000
    assert report["audit"]["members"] == 300


def test_past_the_member_cap_everyone_is_counted_and_the_active_are_mirrored() -> None:
    # Review finding: the report stopped at the first 500 stores in actor
    # order, so later members never synced and the rollup left them out.
    from core.domain.memory import MemberStore
    from core.domain.memory.models import MemoryRecord
    from integrations.webapp_knowledge.snapshot import build_snapshot

    def store(actor: str, day: int) -> MemberStore:
        stamp = f"2026-10-{day:02d}T00:00:00+00:00"
        record = MemoryRecord(
            slug="a-fact",
            memory_type=MemoryType.INFRASTRUCTURE,
            description="A fact",
            created_at=stamp,
            updated_at=stamp,
            body="x",
        )
        return MemberStore(actor=actor, directory=Path("/unused"), records=(record,), usage={})

    # Actor order puts the most recently active member last.
    stores = [store("U_A", 1), store("U_B", 2), store("U_C", 3), store("U_D", 4)]

    report = build_snapshot(_ORG, stores=stores, max_members=2)

    assert report["audit"]["members"] == 4 and report["audit"]["memories"] == 4
    assert [member["actor"] for member in report["members"]] == ["U_D", "U_C"]


def _big_store(actor: str, *, memories: int = 50) -> Any:
    from core.domain.memory import MemberStore
    from core.domain.memory.models import MemoryRecord

    records = tuple(
        MemoryRecord(
            slug=f"fact-{i}",
            memory_type=MemoryType.INFRASTRUCTURE,
            description=f"Fact {i}",
            created_at="2026-10-01T00:00:00+00:00",
            updated_at=f"2026-10-01T00:00:{i % 60:02d}+00:00",
            body="line of notes\n" * 290,
        )
        for i in range(memories)
    )
    return MemberStore(actor=actor, directory=Path("/unused"), records=records, usage={})


def test_a_large_organization_costs_about_one_report_of_memory() -> None:
    # Review finding: every listed store was held and the whole report built
    # before its size was checked, so a big organization cost the gateway
    # hundreds of megabytes on every pass.
    import tracemalloc

    from integrations.webapp_knowledge.snapshot import build_snapshot

    def stores() -> Any:  # 300 members, ~200 KB of memories each: ~60 MB in all
        for i in range(300):
            yield _big_store(f"U{i:04d}")

    tracemalloc.start()
    try:
        report = build_snapshot(_ORG, stores=stores(), max_bytes=500_000)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 15_000_000
    assert report["audit"]["members"] == 300 and len(report["members"]) == 300


@pytest.mark.parametrize("max_bytes", [3_000, 40_000, 400_000, 4_000_000])
def test_the_report_is_built_to_its_exact_budget(max_bytes: int) -> None:
    import json

    from integrations.webapp_knowledge.snapshot import build_snapshot

    save_workspace_entry(
        slug="organization",
        memory_type=MemoryType.ORGANIZATION,
        description="Acme",
        body="Acme sells payment terminals.\n" * 100,
    )
    stores = [_big_store(f"U{i:02d}", memories=i % 7 + 1) for i in range(40)]

    report = build_snapshot(_ORG, stores=stores, max_bytes=max_bytes)

    assert len(json.dumps(report, separators=(",", ":")).encode()) <= max_bytes
    assert report["audit"]["members"] == 40
