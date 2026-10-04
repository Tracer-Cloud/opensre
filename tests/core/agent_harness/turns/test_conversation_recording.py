"""Unit tests for record_conversation_turn.

Covers append, window shrink when over the message cap, and in-place list
update via ``[:]`` — without live API, network, or e2e harnesses.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.constants.conversation_history import OPENSRE_STRUCTURED_HISTORY_ENV
from core.agent_harness.turns.conversation_recording import record_conversation_turn
from core.state import history_window_messages
from core.state.transcript_window import SESSION_SUMMARY_PREFIX


def _session(messages: list[tuple[str, str]] | None = None) -> SimpleNamespace:
    return SimpleNamespace(cli_agent_messages=list(messages or []))


def _turns(n: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i in range(1, n + 1):
        out.append(("user", f"question {i}"))
        out.append(("assistant", f"answer {i}"))
    return out


def test_append_turn_adds_user_and_assistant() -> None:
    session = _session()

    record_conversation_turn(session, "hello", "hi there")

    assert session.cli_agent_messages == [
        ("user", "hello"),
        ("assistant", "hi there"),
    ]


def test_append_turn_extends_existing_history() -> None:
    session = _session([("user", "prior"), ("assistant", "reply")])

    record_conversation_turn(session, "follow-up", "noted")

    assert session.cli_agent_messages == [
        ("user", "prior"),
        ("assistant", "reply"),
        ("user", "follow-up"),
        ("assistant", "noted"),
    ]


def test_no_shrink_when_within_window() -> None:
    # The window is even (turns * 2); leave room for one more turn.
    turns_within = (history_window_messages() // 2) - 1
    session = _session(_turns(turns_within))
    before_len = len(session.cli_agent_messages)

    record_conversation_turn(session, "last question", "last answer")

    assert len(session.cli_agent_messages) == before_len + 2
    assert len(session.cli_agent_messages) <= history_window_messages()
    assert session.cli_agent_messages[-2:] == [
        ("user", "last question"),
        ("assistant", "last answer"),
    ]
    assert not any(
        content.startswith(SESSION_SUMMARY_PREFIX) for _, content in session.cli_agent_messages
    )


def test_window_shrinks_when_too_long() -> None:
    # Fill the window exactly, then one more turn overflows and must compact.
    session = _session(_turns(history_window_messages() // 2))
    assert len(session.cli_agent_messages) == history_window_messages()

    record_conversation_turn(session, "overflow question", "overflow answer")

    assert len(session.cli_agent_messages) <= history_window_messages()
    role, content = session.cli_agent_messages[0]
    assert role == "assistant"
    assert content.startswith(SESSION_SUMMARY_PREFIX)
    assert session.cli_agent_messages[-2:] == [
        ("user", "overflow question"),
        ("assistant", "overflow answer"),
    ]


def test_update_is_in_place_same_list_object() -> None:
    """Compaction must assign via ``[:]`` so external aliases keep seeing updates."""
    session = _session(_turns(history_window_messages() // 2))
    messages = session.cli_agent_messages
    list_id = id(messages)

    record_conversation_turn(session, "overflow question", "overflow answer")

    assert session.cli_agent_messages is messages
    assert id(session.cli_agent_messages) == list_id
    assert len(messages) <= history_window_messages()
    assert messages[0][1].startswith(SESSION_SUMMARY_PREFIX)
    assert messages[-1] == ("assistant", "overflow answer")


class _EvidenceStore:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def append_custom_message(
        self, session_id: str, *, custom_type: str, content: object, display: bool = True
    ) -> str:
        self.records.append(
            {"session_id": session_id, "type": custom_type, "content": content, "display": display}
        )
        return "entry"


def _evidence_session() -> SimpleNamespace:
    return SimpleNamespace(
        cli_agent_messages=[],
        turn_evidence=[],
        store=_EvidenceStore(),
        session_id="session-1",
    )


_TOOL_ITEMS = (
    {
        "kind": "assistant",
        "text": "",
        "tool_calls": [{"id": "call_1", "name": "github_cli", "input": {"args": "run list"}}],
    },
    {
        "kind": "tool_results",
        "results": [{"id": "call_1", "name": "github_cli", "content": "run 9312 failed"}],
    },
)


def test_turn_evidence_is_kept_and_persisted_with_the_transcript() -> None:
    session = _evidence_session()

    record_conversation_turn(session, "which run failed?", "Run 9312.", tool_items=_TOOL_ITEMS)

    [evidence] = session.turn_evidence
    assert evidence.user_text == "which run failed?"
    assert evidence.assistant_text == "Run 9312."
    assert evidence.has_tool_activity
    [record] = session.store.records
    assert record["type"] == "turn_evidence"
    assert record["content"] == evidence.to_json()


def test_text_history_fallback_records_no_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_STRUCTURED_HISTORY_ENV, "0")
    session = _evidence_session()

    record_conversation_turn(session, "which run failed?", "Run 9312.", tool_items=_TOOL_ITEMS)

    assert session.turn_evidence == []
    assert session.store.records == []
