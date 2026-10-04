"""Record a conversation turn on a session, keeping it within the window."""

from __future__ import annotations

from core.agent_harness.ports import SessionState
from core.state import MAX_CONVERSATION_MESSAGES
from core.state.transcript_window import compact_messages_to_window


def record_conversation_turn(session: SessionState, user_text: str, assistant_text: str) -> None:
    """Append one user/assistant exchange and compact to the message window.

    The single write path for ``session.cli_agent_messages`` in the turn
    engine; sessions backed by ``MutableAgentState`` apply the same window
    through ``record_turn``. After each turn, schedules a best-effort memory
    extraction so durable facts are saved without waiting for session exit.
    """
    session.cli_agent_messages.append(("user", user_text))
    session.cli_agent_messages.append(("assistant", assistant_text))
    if len(session.cli_agent_messages) > MAX_CONVERSATION_MESSAGES:
        session.cli_agent_messages[:] = compact_messages_to_window(
            session.cli_agent_messages, max_messages=MAX_CONVERSATION_MESSAGES
        )
    _schedule_turn_memory_extraction(session)


def _schedule_turn_memory_extraction(session: SessionState) -> None:
    try:
        from core.agent_harness.session.memory_extraction import schedule_memory_extraction

        messages = list(getattr(session, "cli_agent_messages", []) or [])
        schedule_memory_extraction(
            messages,
            session_id=session.session_id,
            wait_for_completion=False,
        )
    except Exception:
        # Never let memory bookkeeping break turn recording.
        return


__all__ = ["record_conversation_turn"]
