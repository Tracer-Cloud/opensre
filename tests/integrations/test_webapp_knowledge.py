"""The gateway half of the Knowledge page: one member's memories stay theirs.

The mirror carries every member's memories to the web app, and a member can
forget one from the page. These pin that a forget touches only the asking
member's store, that a redelivered request is acknowledged again without
being applied again, that an unchanged mirror is not resent until the
heartbeat, and that an older web app without the routes is harmless.
"""

from __future__ import annotations

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
from integrations.webapp_scheduled_loops.request_ledger import RequestLedger

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
