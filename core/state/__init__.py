"""Shared agent state: the mutable per-session conversation store.

Holds the mutable per-session agent store reached through ``session.agent``,
the structured record of each completed turn, and the transcript-window
compaction helpers.
"""

from __future__ import annotations

from core.state.agent_state import (
    MAX_CONVERSATION_MESSAGES,
    MAX_CONVERSATION_TURNS,
    AgentMessageRole,
    MutableAgentState,
    history_window_messages,
)
from core.state.turn_evidence import TurnEvidence, match_turn_evidence

__all__ = [
    "MAX_CONVERSATION_MESSAGES",
    "MAX_CONVERSATION_TURNS",
    "AgentMessageRole",
    "MutableAgentState",
    "TurnEvidence",
    "history_window_messages",
    "match_turn_evidence",
]
