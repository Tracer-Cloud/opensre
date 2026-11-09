"""Durable gateway security-audit JSONL."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.storage import security_audit


def test_audit_security_action_appends_jsonl(tmp_path: Path, monkeypatch: object) -> None:
    path = tmp_path / "security-audit.jsonl"
    monkeypatch.setattr(security_audit, "security_audit_path", lambda: path)

    security_audit.audit_security_action(
        action="investigation.create",
        platform="http",
        actor_id="user_1",
        resource_type="investigation",
        resource_id="inv_1",
        outcome="queued",
    )

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "investigation.create"
    assert record["actor_id"] == "user_1"
    assert record["resource_id"] == "inv_1"
    assert record["outcome"] == "queued"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_approval_broker_writes_create_and_resolve(tmp_path: Path, monkeypatch: object) -> None:
    path = tmp_path / "security-audit.jsonl"
    monkeypatch.setattr(security_audit, "security_audit_path", lambda: path)

    broker = ApprovalBroker()
    approval_id = broker.create(platform="slack", chat_id="C123")
    assert broker.resolve(approval_id, approved=True, decided_by="U123") is True

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    actions = [r["action"] for r in records]
    assert actions == ["approval.create", "approval.resolve"]
    assert records[1]["outcome"] == "approved"
    assert records[1]["actor_id"] == "U123"
    assert records[0]["platform"] == "slack"
    assert records[0]["chat_id"] == "C123"
    assert records[1]["platform"] == "slack"
    assert records[1]["chat_id"] == "C123"


def test_approval_broker_writes_expire(tmp_path: Path, monkeypatch: object) -> None:
    path = tmp_path / "security-audit.jsonl"
    monkeypatch.setattr(security_audit, "security_audit_path", lambda: path)

    broker = ApprovalBroker()
    approval_id = broker.create()
    approved, decided_by = broker.wait(approval_id, timeout=0.01)

    assert approved is False
    assert decided_by == ""
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["action"] for r in records] == ["approval.create", "approval.expire"]
    assert records[1]["outcome"] == "denied"
    assert records[1]["resource_id"] == approval_id


def test_audit_write_failure_is_soft(tmp_path: Path, monkeypatch: object) -> None:
    path = tmp_path / "readonly" / "security-audit.jsonl"
    path.parent.mkdir(parents=True)
    path.parent.chmod(stat.S_IREAD | stat.S_IEXEC)
    monkeypatch.setattr(security_audit, "security_audit_path", lambda: path)

    try:
        security_audit.audit_security_action(
            action="investigation.create",
            platform="http",
            resource_type="investigation",
            resource_id="inv_soft",
            outcome="queued",
        )
    finally:
        path.parent.chmod(0o700)

    assert not path.exists()


def test_non_serializable_detail_does_not_raise(tmp_path: Path, monkeypatch: object) -> None:
    path = tmp_path / "security-audit.jsonl"
    monkeypatch.setattr(security_audit, "security_audit_path", lambda: path)

    class _Boom:
        def __str__(self) -> str:
            raise TypeError("cannot stringify")

    security_audit.audit_security_action(
        action="investigation.create",
        detail={"bad": _Boom()},
        outcome="queued",
    )
    assert not path.exists()
