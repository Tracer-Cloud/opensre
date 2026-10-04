from __future__ import annotations

from core.state import MutableAgentState, TurnEvidence, history_window_messages
from core.state.transcript_window import SESSION_SUMMARY_PREFIX


def test_record_turn_appends_transcript() -> None:
    state = MutableAgentState()
    state.record_turn("hello", "hi there")
    assert state.messages == [("user", "hello"), ("assistant", "hi there")]


def test_message_cap_compacts_oldest_into_summary() -> None:
    window = history_window_messages()
    state = MutableAgentState()
    for index in range(window + 3):
        state.record_turn(f"user {index}", f"assistant {index}")

    assert len(state.messages) <= window
    role, content = state.messages[0]
    assert role == "assistant"
    assert content.startswith(SESSION_SUMMARY_PREFIX)
    assert "user 0" in content
    assert state.messages[-1] == ("assistant", f"assistant {window + 2}")


def test_messages_setter_replaces_and_compacts() -> None:
    window = history_window_messages()
    state = MutableAgentState()
    state.messages = [("user", str(i)) for i in range(window + 5)]
    assert len(state.messages) <= window
    assert state.messages[0][1].startswith(SESSION_SUMMARY_PREFIX)


def test_evidence_is_bounded_by_the_transcript_it_describes() -> None:
    state = MutableAgentState()
    for index in range(10):
        state.record_turn(
            f"user {index}",
            f"assistant {index}",
            evidence=TurnEvidence(user_text=f"user {index}", assistant_text=f"assistant {index}"),
        )

    state.messages = state.messages[-4:]

    # Two pairs remain, plus a little slack for records restored out of step.
    assert len(state.turn_evidence) <= 2 + 4
    assert state.turn_evidence[-1].assistant_text == "assistant 9"


def test_clear_empties_transcript_and_evidence() -> None:
    state = MutableAgentState()
    state.record_turn("u", "a")
    state.turn_evidence = [TurnEvidence(user_text="u", assistant_text="a")]

    state.clear()

    assert state.messages == []
    assert state.turn_evidence == []
