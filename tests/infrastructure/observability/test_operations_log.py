"""Tests for the local operations JSONL log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.constants import (
    OPENSRE_OPERATIONS_LOG_DISABLED_ENV,
    OPENSRE_OPERATIONS_LOG_PATH_ENV,
)
from infrastructure.observability.operations_log import read_operations, record_operation


def test_record_operation_writes_redacted_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "operations.jsonl"
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(log_path))

    record_operation(
        "test_event",
        {
            "api_key": "secret-token",
            "payload": {"ok": True},
            "path": tmp_path,
        },
    )

    records = read_operations(path=log_path)
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "test_event"
    assert record["schema_version"] == 1
    assert record["data"]["api_key"] == "[redacted]"
    assert record["data"]["payload"] == {"ok": True}
    assert record["data"]["path"] == str(tmp_path)
    assert "secret-token" not in json.dumps(record)


def test_record_operation_honors_disabled_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "operations.jsonl"
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_DISABLED_ENV, "1")

    record_operation("test_event", {"value": "ok"}, path=log_path)

    assert not log_path.exists()


def test_read_operations_returns_recent_entries(tmp_path: Path) -> None:
    log_path = tmp_path / "operations.jsonl"
    for index in range(3):
        record_operation(f"event_{index}", path=log_path)

    assert read_operations(limit=0, path=log_path) == []
    records = read_operations(limit=2, path=log_path)
    assert [record["event"] for record in records] == ["event_1", "event_2"]
