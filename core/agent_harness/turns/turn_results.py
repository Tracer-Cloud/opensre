"""Neutral turn-result models for the agentic turn engine.

These are surface-agnostic "facts only" records: they describe what a turn did
(actions planned/executed, the assistant response) without any terminal,
session, or analytics coupling. The interactive shell's accounting layer
(:mod:`surfaces.interactive_shell.runtime.core.turn_accounting`) consumes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Distinguishes the two zero-count outcomes that need different analytics:
# a normal tool-calling run that completed without planning actions ("completed"),
# versus a run that never produced actions because it failed/overflowed ("not_run").
ToolCallingAccountingStatus = Literal["completed", "not_run"]

# Host soft-timeout / ``/stop`` — orchestrator skips gather/answer on this intent.
FINAL_INTENT_CANCELLED = "cli_agent_cancelled"


@dataclass(frozen=True)
class ToolCallingTurnResult:
    """Facts-only outcome of the action tool-calling phase of a turn."""

    planned_count: int
    executed_count: int
    executed_success_count: int
    has_unhandled_clause: bool
    handled: bool
    response_text: str = ""
    response_streamed: bool = False
    accounting_status: ToolCallingAccountingStatus = "completed"
    hit_iteration_cap: bool = False
    #: Host soft-timeout / stop asked the action phase to halt (shell/gateway).
    cancelled: bool = False
    #: Provider-reported usage summed over the phase's model calls (0 when unreported).
    input_tokens: int = 0
    output_tokens: int = 0
    #: Provider-visible tool outputs for session-goal review, including failures.
    tool_evidence: str = ""
    #: Qualifying successes; None for hosts that only supply aggregate counts.
    evidence_success_count: int | None = None


@dataclass(frozen=True)
class TurnResult:
    """Outcome of one tool-calling agent turn."""

    final_intent: str
    action_result: ToolCallingTurnResult
    assistant_response_text: str = ""

    @property
    def answered(self) -> bool:
        """Whether the agent produced user-facing text."""
        return bool(self.primary_response_text)

    @property
    def cancelled(self) -> bool:
        """True when the host cancelled mid-turn (timeout / stop)."""
        return self.final_intent == FINAL_INTENT_CANCELLED or self.action_result.cancelled

    @property
    def primary_response_text(self) -> str:
        """Assistant text, falling back to the action-phase response when empty."""
        return (self.assistant_response_text or self.action_result.response_text).strip()


__all__ = [
    "FINAL_INTENT_CANCELLED",
    "ToolCallingAccountingStatus",
    "ToolCallingTurnResult",
    "TurnResult",
]
