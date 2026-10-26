"""Tests for the in-memory session storage backend."""

from __future__ import annotations

from core.agent_harness.session import InMemorySessionStore
from surfaces.interactive_shell.session import Session


def _session(storage: InMemorySessionStore) -> Session:
    return Session(store=storage)


def test_open_then_record_appends_turn() -> None:
    storage = InMemorySessionStore()
    session = _session(storage)
    storage.open_session(session)
    session.record("chat", "hello world")

    records = storage.read(session.session_id)
    assert records[0]["type"] == "session"
    assert records[0]["version"] == 2
    turns = [r for r in records if r["type"] == "custom_message"]
    assert turns[0]["custom_type"] == "turn_stub"
    assert turns[0]["kind"] == "chat"
    assert turns[0]["text"] == "hello world"


def test_record_noop_when_not_opened() -> None:
    storage = InMemorySessionStore()
    session = _session(storage)
    session.record("chat", "hi")  # no open_session
    assert storage.read(session.session_id) == []


def test_flush_writes_session_end_with_counts() -> None:
    storage = InMemorySessionStore()
    session = _session(storage)
    storage.open_session(session)
    session.record("chat", "q1")
    session.record("alert", "boom")
    storage.flush(session)

    leaf = storage.read(session.session_id)[-1]
    assert leaf["type"] == "leaf"
    assert leaf["total_turns"] == 2


def test_flush_deletes_empty_session() -> None:
    storage = InMemorySessionStore()
    session = _session(storage)
    storage.open_session(session)
    storage.flush(session)
    assert storage.read(session.session_id) == []


def test_flush_is_idempotent() -> None:
    storage = InMemorySessionStore()
    session = _session(storage)
    storage.open_session(session)
    session.record("chat", "hi")
    storage.flush(session)
    storage.flush(session)
    leaves = [r for r in storage.read(session.session_id) if r["type"] == "leaf"]
    assert len(leaves) == 1


def test_append_turn_detail_writes_message_entries() -> None:
    storage = InMemorySessionStore()
    session = _session(storage)
    storage.open_session(session)
    storage.append_turn_detail(session.session_id, "chat", "hello", response="hi")

    records = storage.read(session.session_id)
    messages = [r for r in records if r["type"] == "message"]
    assert [(r["role"], r["content"]) for r in messages] == [("user", "hello"), ("assistant", "hi")]


def test_append_tool_call_reopens_finalized_session() -> None:
    storage = InMemorySessionStore()
    session = _session(storage)
    storage.open_session(session)
    session.record("chat", "do a thing")
    storage.flush(session)
    storage.append_tool_call(session.session_id, tool="t", arguments={}, result="{}", ok=True)

    records = storage.read(session.session_id)
    assert any(r["type"] == "tool_call" for r in records)
    assert any(r["type"] == "tool_result" for r in records)
