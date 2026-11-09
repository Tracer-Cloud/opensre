"""Tests for prompt trace persistence."""

from __future__ import annotations

from core.agent_harness.session import InMemorySessionStore
from infrastructure.observability.trace.prompts import persist_turn_system_prompt
from surfaces.interactive_shell.session import Session


def test_persist_turn_system_prompt_writes_system_message() -> None:
    session = Session(store=InMemorySessionStore())
    session.store.open_session(session)

    persist_turn_system_prompt(
        session,
        phase="action_agent",
        system_prompt="  you are the action agent  ",
    )

    records = session.store.read(session.session_id)
    system_rows = [
        row for row in records if row.get("type") == "message" and row.get("role") == "system"
    ]
    assert len(system_rows) == 1
    assert system_rows[0]["content"] == "you are the action agent"
    assert system_rows[0]["metadata"]["kind"] == "action_agent"
    assert system_rows[0]["metadata"]["debug"] == "system_prompt"


def test_persist_turn_system_prompt_noop_on_blank() -> None:
    session = Session(store=InMemorySessionStore())
    session.store.open_session(session)

    persist_turn_system_prompt(session, phase="action_agent", system_prompt="   ")

    records = session.store.read(session.session_id)
    assert all(row.get("type") != "message" or row.get("role") != "system" for row in records)
