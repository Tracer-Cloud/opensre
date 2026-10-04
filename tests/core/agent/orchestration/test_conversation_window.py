"""A fact stated early in a long conversation must stay visible to the model.

Drives the real turn-recording paths (orchestrator and action driver) through a
session whose ``cli_agent_messages`` aliases ``agent.messages``, mirroring the
production session wiring, and checks the history block the assistant prompt
renders from.
"""

from __future__ import annotations

import pytest

from config.constants.conversation_history import OPENSRE_STRUCTURED_HISTORY_ENV
from core.agent_harness.prompts.memory.conversation import format_recent_conversation
from core.agent_harness.turns.action_driver import _persist_tool_calling_error
from core.agent_harness.turns.conversation_recording import (
    record_conversation_turn as _record_answer_turn,
)
from core.state import MAX_CONVERSATION_MESSAGES, MutableAgentState
from core.state.transcript_window import SESSION_SUMMARY_PREFIX


@pytest.fixture(autouse=True)
def _text_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests pin the text-history fallback (``OPENSRE_STRUCTURED_HISTORY=0``)."""
    monkeypatch.setenv(OPENSRE_STRUCTURED_HISTORY_ENV, "0")


_FACT = "prod-eu-42"


class _Session:
    def __init__(self) -> None:
        self.agent = MutableAgentState()

    @property
    def cli_agent_messages(self) -> list[tuple[str, str]]:
        return self.agent.messages


def _run_short_turns(session: _Session, count: int, start: int = 2) -> None:
    for i in range(start, start + count):
        _record_answer_turn(
            session,
            f"Turn {i}: what's the status of service-{i}?",
            f"service-{i} looks healthy.",
        )


def test_turn_one_fact_survives_fourteen_turns() -> None:
    session = _Session()
    _record_answer_turn(
        session,
        f"my cluster is named {_FACT}",
        f"Got it — your cluster is {_FACT}.",
    )

    _run_short_turns(session, count=13)

    history_block = format_recent_conversation(session.cli_agent_messages)
    assert _FACT in history_block
    assert len(session.cli_agent_messages) <= MAX_CONVERSATION_MESSAGES


def test_window_holds_single_summary_over_long_sessions() -> None:
    session = _Session()
    _record_answer_turn(session, f"remember {_FACT}", "noted")

    _run_short_turns(session, count=40)

    messages = session.cli_agent_messages
    summaries = [m for m in messages if m[1].startswith(SESSION_SUMMARY_PREFIX)]
    assert len(summaries) == 1
    assert messages[0] == summaries[0]
    assert _FACT in messages[0][1]
    assert len(messages) <= MAX_CONVERSATION_MESSAGES


def test_tool_error_recording_compacts_instead_of_dropping() -> None:
    session = _Session()
    _record_answer_turn(session, f"remember {_FACT}", "noted")

    for i in range(2, 16):
        _persist_tool_calling_error(session, f"turn {i} request", f"turn {i} error")

    history_block = format_recent_conversation(session.cli_agent_messages)
    assert _FACT in history_block
    assert len(session.cli_agent_messages) <= MAX_CONVERSATION_MESSAGES
