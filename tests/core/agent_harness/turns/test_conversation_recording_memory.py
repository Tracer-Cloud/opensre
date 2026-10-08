"""Recording turns drives background memory extraction every few non-demo turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import core.agent_harness.session.memory_extraction as extraction
from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV
from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.session.memory_turns import EXTRACTION_TURN_INTERVAL, demo_turns
from core.agent_harness.turns.conversation_recording import record_conversation_turn
from infrastructure.analytics.repl_context import bind_prompt_turn_id, reset_prompt_turn_id


@dataclass
class _FakeSession:
    session_id: str
    cli_agent_messages: list[tuple[str, str]] = field(default_factory=list)
    last_command_observation: Any = None
    active_skill: str | None = None


@pytest.fixture
def scheduled(monkeypatch: pytest.MonkeyPatch) -> list[extraction.ExtractionJob]:
    monkeypatch.delenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, raising=False)
    jobs: list[extraction.ExtractionJob] = []
    monkeypatch.setattr(extraction, "_schedule_coalesced", jobs.append)
    return jobs


def _record(session: _FakeSession, index: int) -> None:
    token = bind_prompt_turn_id(f"{session.session_id}-turn-{index}")
    try:
        record_conversation_turn(session, f"question {index}", f"answer {index}")
    finally:
        reset_prompt_turn_id(token)


def test_a_pass_is_scheduled_once_per_interval_of_turns(
    scheduled: list[extraction.ExtractionJob],
) -> None:
    session = _FakeSession(session_id="s-interval")

    for index in range(EXTRACTION_TURN_INTERVAL - 1):
        _record(session, index)
    assert scheduled == []

    _record(session, EXTRACTION_TURN_INTERVAL - 1)

    [job] = scheduled
    assert (job.session_id, job.final) == ("s-interval", False)
    assert job.messages == tuple(session.cli_agent_messages)


def test_demo_turns_are_tagged_and_do_not_advance_the_interval(
    scheduled: list[extraction.ExtractionJob],
) -> None:
    session = _FakeSession(session_id="s-demo-turns", active_skill=ONBOARDING_SKILL_NAME)
    for index in range(2 * EXTRACTION_TURN_INTERVAL):
        _record(session, index)

    assert scheduled == []
    assert "s-demo-turns-turn-0" in demo_turns("s-demo-turns").turn_ids


def test_memory_bookkeeping_never_breaks_turn_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_session: object) -> None:
        raise RuntimeError("memory store unavailable")

    monkeypatch.setattr(extraction, "record_turn_for_memory", _boom)
    session = _FakeSession(session_id="s-boom")

    record_conversation_turn(session, "hello", "hi")

    assert session.cli_agent_messages == [("user", "hello"), ("assistant", "hi")]
