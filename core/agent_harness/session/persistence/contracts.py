"""Session persistence contracts for the interactive shell.

These protocols decouple the in-memory session object (:class:`Session`)
and the slash-command surfaces from any concrete persistence backend. Two
roles are kept deliberately separate, mirroring a storage-vs-repository split:

- :class:`SessionStore` — per-session lifecycle and entry writes for a single
  logical session (open, append, flush, reopen). Backends: JSONL (production)
  and in-memory (tests).
- :class:`SessionRepo` — cross-session queries over every stored session
  (list recent, load one for ``/resume``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from core.state import MutableAgentState


class RestoreContextKey(StrEnum):
    """Keys of the restore-context payload written by the repo and read on load.

    Shared here so the write side (``SessionRepo``) and the read side
    (``restore_context``) cannot drift: a rename lands in one spelling.
    """

    CLI_AGENT_MESSAGES = "cli_agent_messages"
    ACCUMULATED_CONTEXT = "accumulated_context"
    SESSION_GOAL_STATE = "session_goal_state"
    SESSION_GOAL_CONTROLS = "session_goal_controls"
    TASK_PLAN_STATE = "task_plan_state"
    PENDING_USER_CHOICE_STATE = "pending_user_choice_state"
    HISTORY = "history"
    TURN_EVIDENCE = "turn_evidence"


#: ``custom_message`` type of the per-turn evidence record (tool calls and
#: bounded results) that structured history replays on resume.
TURN_EVIDENCE_CUSTOM_TYPE = "turn_evidence"

# Turn kinds that represent user-initiated chat messages. Session.record()
# is called with the turn kind, not a normalized "chat" label, so this set must
# cover all kinds that produce conversational turns.
CHAT_KINDS: frozenset[str] = frozenset({"chat", "cli_agent", "follow_up"})

# A single JSONL entry carrying the goal and task-plan snapshots changed by a
# durable goal control.  Restore treats it as an atomic pair.
SESSION_GOAL_CONTROL_STATE_CUSTOM_TYPE = "session_goal_control_state"


class SessionPersistenceSource(Protocol):
    """Fields a :class:`SessionStore` backend reads off a live session."""

    session_id: str
    started_at: float
    agent: MutableAgentState
    accumulated_context: dict[str, Any]


@runtime_checkable
class SessionStore(Protocol):
    """Per-session persistence backend for one logical session."""

    def open_session(self, session: SessionPersistenceSource) -> None:
        raise NotImplementedError

    def append_turn(self, session: SessionPersistenceSource, kind: str, text: str) -> None:
        raise NotImplementedError

    def append_session_name(self, session_id: str, name: str) -> str:
        """Append a session-name override; an empty name restores the derived title."""

    def append_turn_detail(
        self,
        session_id: str,
        kind: str,
        prompt: str,
        *,
        response: str | None = None,
        turn_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        latency_ms: int | None = None,
        system_prompt: str | None = None,
    ) -> None:
        raise NotImplementedError

    def append_tool_call(
        self,
        session_id: str,
        *,
        tool: str,
        arguments: dict[str, Any],
        result: str,
        ok: bool,
        source: str | None = None,
        tool_call_id: str | None = None,
        sidecar: bool = False,
    ) -> None:
        """Record one executed tool call; with ``tool_call_id`` it commits a WAL intent."""

    def append_tool_intent(
        self,
        session_id: str,
        *,
        tool: str,
        arguments: dict[str, Any],
        tool_call_id: str,
        seq: int,
        user_text: str | None = None,
    ) -> str:
        """Durably record a tool call about to execute (WAL intent, fsynced)."""

    def append_tool_update(
        self,
        session_id: str,
        *,
        tool: str,
        update: Any,
        tool_call_id: str | None = None,
    ) -> str:
        raise NotImplementedError

    def append_compaction(
        self,
        session_id: str,
        *,
        summary: str,
        first_kept_entry_id: str,
        before_chars: int,
        after_chars: int,
        before_tokens: int | None = None,
        after_tokens: int | None = None,
        replacement_messages: list[list[str]] | None = None,
        replacement_evidence: list[dict[str, Any]] | None = None,
    ) -> str:
        """Record a compaction; with replacements, it restarts the transcript on resume."""

    def flush(self, session: SessionPersistenceSource) -> None:
        raise NotImplementedError

    def flush_session_goal_control_state(self, session: SessionPersistenceSource) -> None:
        """Persist every resumable state field changed by a goal control."""

    def append_session_goal_control(self, session_id: str, reason: str) -> str:
        """Durably record a goal control and return its acknowledgement id."""

    def complete_session_goal_control(self, session_id: str, control_id: str) -> None:
        """Durably mark one recorded goal control as applied."""

    def reopen_session(self, session_id: str) -> None:
        raise NotImplementedError


@runtime_checkable
class SessionRepo(Protocol):
    """Cross-session query/lifecycle surface over all stored sessions."""

    def load_recent(
        self,
        n: int = 20,
        *,
        require_conversation: bool = False,
    ) -> list[dict[str, Any]]:
        """Return recent sessions, optionally counting only conversations toward ``n``."""

    def count_prefix_matches(self, prefix: str) -> int:
        raise NotImplementedError

    def load_session(self, session_id_prefix: str) -> dict[str, Any] | None:
        raise NotImplementedError
