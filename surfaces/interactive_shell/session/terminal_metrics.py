"""Per-session terminal analytics, extracted from the session state object.

Groups the interactive-shell turn/intervention counters into one cohesive
accumulator so the session state class does not carry analytics fields and
methods directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict

from config.strict_config import StrictConfigModel

InterventionKind = Literal["ctrl_c", "correction"]


class TerminalMetricsSnapshot(StrictConfigModel):
    """Immutable per-turn analytics snapshot returned from ``record_turn``.

    Pure value; the mutable :class:`TerminalMetrics` accumulator produces one
    of these after each turn for the caller to render/emit.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_index: int
    fallback_count: int
    action_success_percent: float | None
    fallback_rate_percent: float


@dataclass
class TerminalMetrics:
    """Mutable session-level counters for interactive-shell analytics."""

    turn_count: int = 0
    fallback_count: int = 0
    actions_executed_count: int = 0
    actions_success_count: int = 0
    ctrl_c_intervention_count: int = 0
    """Incremented when the user Ctrl-Cs an active turn. Bare-prompt
    Ctrl-C with no agent running is intentionally not counted."""
    correction_intervention_count: int = 0
    """Incremented when a follow-up/new-alert message starts with a correction cue."""

    def record_turn(
        self,
        *,
        executed_count: int,
        executed_success_count: int,
        fallback_to_llm: bool,
    ) -> TerminalMetricsSnapshot:
        """Update aggregate terminal metrics and return a stable snapshot."""
        self.turn_count += 1
        self.actions_executed_count += max(0, executed_count)
        self.actions_success_count += max(0, executed_success_count)
        if fallback_to_llm:
            self.fallback_count += 1
        action_success_percent = (
            100.0 * self.actions_success_count / self.actions_executed_count
            if self.actions_executed_count > 0
            else None
        )
        fallback_rate_percent = 100.0 * self.fallback_count / self.turn_count
        return TerminalMetricsSnapshot(
            turn_index=self.turn_count,
            fallback_count=self.fallback_count,
            action_success_percent=action_success_percent,
            fallback_rate_percent=fallback_rate_percent,
        )

    def record_intervention(self, kind: InterventionKind) -> None:
        """Increment the per-kind intervention counter (Ctrl-C or correction)."""
        if kind == "ctrl_c":
            self.ctrl_c_intervention_count += 1
        elif kind == "correction":
            self.correction_intervention_count += 1
        else:
            raise ValueError(f"Unknown intervention kind: {kind!r}")

    def reset(self) -> None:
        """Zero all counters (used by ``/new``)."""
        self.turn_count = 0
        self.fallback_count = 0
        self.actions_executed_count = 0
        self.actions_success_count = 0
        self.ctrl_c_intervention_count = 0
        self.correction_intervention_count = 0


__all__ = ["InterventionKind", "TerminalMetrics", "TerminalMetricsSnapshot"]
