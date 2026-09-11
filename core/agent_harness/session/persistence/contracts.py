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
    TASK_PLAN_STATE = "task_plan_state"
    HISTORY = "history"
    WORKING_DIRECTORY = "working_directory"


# Turn kinds that represent user-initiated chat messages. Session.record()
# is called with the turn kind, not a normalized "chat" label, so this set must
# cover all kinds that produce conversational turns.
CHAT_KINDS: frozenset[str] = frozenset({"chat", "cli_agent", "follow_up"})


class SessionPersistenceSource(Protocol):
    """Fields a :class:`SessionStore` backend reads off a live session."""

    session_id: str
    started_at: float
    working_directory: str
    agent: MutableAgentState
    accumulated_context: dict[str, Any]


@runtime_checkable
class SessionStore(Protocol):
    """Per-session persistence backend for one logical session."""

    def open_session(self, session: SessionPersistenceSource) -> None:
        raise NotImplementedError

    def append_turn(self, session: SessionPersistenceSource, kind: str, text: str) -> None:
        raise NotImplementedError

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
    ) -> str:
        raise NotImplementedError

    def append_working_directory(self, session_id: str, working_directory: str) -> None:
        """Persist the session's current working directory on its active branch."""

    def flush(self, session: SessionPersistenceSource) -> None:
        raise NotImplementedError

    def reopen_session(self, session_id: str) -> None:
        raise NotImplementedError


@runtime_checkable
class SessionRepo(Protocol):
    """Cross-session query/lifecycle surface over all stored sessions."""

    def load_recent(self, n: int = 20) -> list[dict[str, Any]]:
        raise NotImplementedError

    def count_prefix_matches(self, prefix: str) -> int:
        raise NotImplementedError

    def load_session(self, session_id_prefix: str) -> dict[str, Any] | None:
        raise NotImplementedError
