"""Record a conversation turn on a session, keeping it within the window."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from typing import Any

from core.agent_harness.ports import SessionState
from core.agent_harness.session.persistence.contracts import TURN_EVIDENCE_CUSTOM_TYPE
from core.state import history_window_messages
from core.state.history_settings import structured_history_enabled
from core.state.transcript_window import compact_messages_to_window


def record_conversation_turn(
    session: SessionState,
    user_text: str,
    assistant_text: str,
    *,
    tool_items: Sequence[Mapping[str, Any]] = (),
    typed_text: str = "",
) -> None:
    """Append one user/assistant exchange and compact to the message window.

    The single write path for ``session.cli_agent_messages`` in the turn
    engine; sessions backed by ``MutableAgentState`` apply the same window
    through ``record_turn``. With structured history on, the turn's tool
    batches (``tool_items``, from ``structured_history.tool_items_from_run``)
    are recorded beside the text and persisted, so later turns and a resumed
    session replay what the tools returned; ``typed_text`` is the message as
    typed when ``user_text`` is its expansion. After each turn, schedules a
    best-effort memory extraction so durable facts are saved without waiting
    for session exit.
    """
    session.cli_agent_messages.append(("user", user_text))
    session.cli_agent_messages.append(("assistant", assistant_text))
    window = history_window_messages()
    if len(session.cli_agent_messages) > window:
        session.cli_agent_messages[:] = compact_messages_to_window(
            session.cli_agent_messages, max_messages=window
        )
    if structured_history_enabled():
        _record_turn_evidence(session, user_text, assistant_text, tool_items, typed_text)
    _schedule_turn_memory_extraction(session)


def _record_turn_evidence(
    session: SessionState,
    user_text: str,
    assistant_text: str,
    tool_items: Sequence[Mapping[str, Any]],
    typed_text: str,
) -> None:
    """Keep the turn's evidence on the session and append it to the session log."""
    from core.agent_harness.turns.structured_history import build_turn_evidence

    records = getattr(session, "turn_evidence", None)
    if not isinstance(records, list):
        return
    evidence = build_turn_evidence(user_text, assistant_text, tool_items, typed_text=typed_text)
    records.append(evidence)
    # Bound the list the same way MutableAgentState does for its own records.
    keep = len(session.cli_agent_messages) // 2 + 4
    if len(records) > keep:
        del records[: len(records) - keep]
    store = getattr(session, "store", None)
    append = getattr(store, "append_custom_message", None)
    session_id = getattr(session, "session_id", "")
    if not callable(append) or not isinstance(session_id, str) or not session_id:
        return
    with contextlib.suppress(Exception):
        append(
            session_id,
            custom_type=TURN_EVIDENCE_CUSTOM_TYPE,
            content=evidence.to_json(),
            display=False,
        )


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
