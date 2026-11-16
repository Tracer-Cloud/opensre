"""Regression tests: a persisted debug system prompt must not suppress the
conversation messages ``flush`` writes from ``session.agent.messages``."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_harness.session import InMemorySessionStore
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.session.persistence.paths import session_path
from infrastructure.observability.trace.prompts import persist_turn_system_prompt
from surfaces.interactive_shell.session import Session


@pytest.fixture
def storage_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPENSRE_HOME", str(tmp_path))
    from config.constants import paths as paths_constants

    monkeypatch.setattr(paths_constants, "OPENSRE_HOME_DIR", tmp_path, raising=False)
    return tmp_path


def test_memory_flush_persists_conversation_despite_debug_prompt() -> None:
    session = Session(store=InMemorySessionStore())
    session.store.open_session(session)
    session.agent.messages = [("user", "hello"), ("assistant", "world")]

    persist_turn_system_prompt(session, phase="action_agent", system_prompt="system prompt")
    session.store.flush(session)

    records = session.store.read(session.session_id)
    chat = [
        rec
        for rec in records
        if rec.get("type") == "message" and rec.get("role") in ("user", "assistant")
    ]
    assert [(rec["role"], rec["content"]) for rec in chat] == [
        ("user", "hello"),
        ("assistant", "world"),
    ]


def test_jsonl_flush_persists_conversation_despite_debug_prompt(storage_home: Path) -> None:
    storage = JsonlSessionStore()
    session: Any = SimpleNamespace(
        session_id="sess-debug-prompt",
        started_at=0.0,
        store=storage,
        agent=SimpleNamespace(messages=[("user", "hello"), ("assistant", "world")]),
        accumulated_context={},
    )
    storage.open_session(session)

    persist_turn_system_prompt(session, phase="action_agent", system_prompt="system prompt")
    storage.flush(session)

    records = [
        json.loads(line)
        for line in session_path(session.session_id).read_text(encoding="utf-8").splitlines()
    ]
    chat = [
        rec
        for rec in records
        if rec.get("type") == "message" and rec.get("role") in ("user", "assistant")
    ]
    assert [(rec["role"], rec["content"]) for rec in chat] == [
        ("user", "hello"),
        ("assistant", "world"),
    ]
