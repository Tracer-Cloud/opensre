"""Durable tool previews retain the Codex-sized prefix and suffix."""

import json
from pathlib import Path

import pytest

from core.agent_harness.session.persistence import paths
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.turns import wal_recorder
from core.events import ToolExecutionEndEvent


def test_wal_persists_a_64_kib_head_tail_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "sessions_dir", lambda: tmp_path)
    store = JsonlSessionStore()
    session = SessionCore(session_id="output-test", started_at=1.0, store=store)
    store.open_session(session)

    def session_store() -> JsonlSessionStore:
        return store

    monkeypatch.setattr(wal_recorder, "default_session_store", session_store)
    record = wal_recorder.wal_event_recorder(session)
    record(
        ToolExecutionEndEvent(
            tool_call_id="call-1",
            tool_name="logs",
            args={},
            result="HEAD" + "x" * 70_000 + "TAIL",
            is_error=False,
            iteration=1,
        )
    )
    records = [
        json.loads(line) for line in (tmp_path / "output-test.jsonl").read_text().splitlines()
    ]
    [result] = [item for item in records if item["type"] == "tool_result"]

    assert result["content"].startswith("HEAD")
    assert result["content"].endswith("TAIL")
    assert "…4472 chars truncated…" in result["content"]
    assert len(result["content"].encode("utf-8")) < 65_636
