"""Cross-turn agent state: the conversation transcript and its turn evidence.

``session.agent`` is a :class:`MutableAgentState` — mutable state that
persists *across* turns. Production reads and writes ``messages`` (transcript),
``turn_evidence`` (what each turn's tools returned), and ``clear()`` only.
Per-turn data (tools, resolved integrations, system prompt, iteration cap) is on
``TurnSnapshot``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from config.constants.conversation_history import STRUCTURED_HISTORY_MAX_TURNS
from core.state.history_settings import structured_history_enabled
from core.state.transcript_window import compact_messages_to_window
from core.state.turn_evidence import TurnEvidence

MAX_CONVERSATION_TURNS = 12
MAX_CONVERSATION_MESSAGES = MAX_CONVERSATION_TURNS * 2

AgentMessageRole = Literal["user", "assistant", "system", "tool"]

#: Evidence records kept beyond the transcript's own pairs, so a pair restored a
#: little out of step still finds its record before trimming catches up.
_EVIDENCE_SLACK = 4


def history_window_messages() -> int:
    """Messages kept verbatim before the oldest fold into the session summary.

    Structured history is bounded by token-based compaction, so its window is a
    wide backstop. The text-only fallback keeps the original 12-turn window.
    """
    if structured_history_enabled():
        return STRUCTURED_HISTORY_MAX_TURNS * 2
    return MAX_CONVERSATION_MESSAGES


class MutableAgentState:
    """Cross-turn agent state: the conversation transcript and its turn evidence.

    Holds only what must survive across turns. Per-turn data (tools, resolved
    integrations, system prompt, iteration cap) lives on ``TurnSnapshot``.
    """

    def __init__(self, *, messages: Sequence[tuple[str, str]] = ()) -> None:
        self._messages: list[tuple[str, str]] = list(messages)
        self._turn_evidence: list[TurnEvidence] = []

    @property
    def messages(self) -> list[tuple[str, str]]:
        return self._messages

    @messages.setter
    def messages(self, value: Sequence[tuple[str, str]]) -> None:
        self._replace_messages(value)

    @property
    def turn_evidence(self) -> list[TurnEvidence]:
        """Structured records of recent turns, oldest first (see :mod:`core.state.turn_evidence`)."""
        return self._turn_evidence

    @turn_evidence.setter
    def turn_evidence(self, value: Sequence[TurnEvidence]) -> None:
        self._turn_evidence = [record for record in value if isinstance(record, TurnEvidence)]
        self._trim_evidence()

    def record_turn(
        self,
        user_message: str,
        assistant_message: str,
        *,
        evidence: TurnEvidence | None = None,
    ) -> None:
        self._messages.append(("user", user_message))
        self._messages.append(("assistant", assistant_message))
        if evidence is not None:
            self._turn_evidence.append(evidence)
        self._compact_messages()

    def clear(self) -> None:
        self._messages.clear()
        self._turn_evidence.clear()

    def _replace_messages(self, messages: Sequence[tuple[str, str]]) -> None:
        self._messages = list(messages)
        self._compact_messages()

    def _compact_messages(self) -> None:
        window = history_window_messages()
        if len(self._messages) > window:
            self._messages[:] = compact_messages_to_window(self._messages, max_messages=window)
        self._trim_evidence()

    def _trim_evidence(self) -> None:
        """Keep no more records than the transcript has pairs (plus a little slack)."""
        keep = len(self._messages) // 2 + _EVIDENCE_SLACK
        if len(self._turn_evidence) > keep:
            del self._turn_evidence[: len(self._turn_evidence) - keep]


__all__ = [
    "MAX_CONVERSATION_MESSAGES",
    "MAX_CONVERSATION_TURNS",
    "AgentMessageRole",
    "MutableAgentState",
    "history_window_messages",
]
