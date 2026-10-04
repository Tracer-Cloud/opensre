"""Tests that conversation turns schedule mid-session memory extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from core.agent_harness.turns.conversation_recording import record_conversation_turn


@dataclass
class _FakeSession:
    session_id: str = "s-turn"
    cli_agent_messages: list[tuple[str, str]] = field(default_factory=list)
    last_command_observation: Any = None


def test_record_conversation_turn_schedules_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[tuple[str, str]], str, bool]] = []

    def _schedule(
        messages: list[tuple[str, str]], *, session_id: str, wait_for_completion: bool = False
    ) -> None:
        calls.append((list(messages), session_id, wait_for_completion))

    monkeypatch.setattr(
        "core.agent_harness.session.memory_extraction.schedule_memory_extraction",
        _schedule,
    )
    session = _FakeSession()
    record_conversation_turn(session, "my name is Vaibhav", "noted")
    assert session.cli_agent_messages == [
        ("user", "my name is Vaibhav"),
        ("assistant", "noted"),
    ]
    assert calls == [(session.cli_agent_messages, "s-turn", False)]
