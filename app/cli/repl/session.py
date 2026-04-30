"""In-memory session state that persists across REPL turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReplSession:
    """Per-REPL-process accumulated state.

    Carries everything we want to persist across individual investigations
    within the same REPL session: previous investigation state (for follow-up
    questions), accumulated infra context (service names, clusters observed),
    trust mode flag, and a short interaction history for /status.
    """

    history: list[dict[str, Any]] = field(default_factory=list)
    """Each entry: {"type": "alert"|"follow_up"|"slash", "text": str, "ok": bool}."""

    last_state: dict[str, Any] | None = None
    """The final AgentState from the most recent investigation, used by follow-ups."""

    accumulated_context: dict[str, Any] = field(default_factory=dict)
    """Reusable infra context — service names, clusters, regions — learned from
    earlier investigations that should seed future ones."""

    trust_mode: bool = False
    """If True, skip any future [Y/n] confirmation prompts for read-only tools.
    (No destructive tools exist today; reserved for forward compatibility.)"""

    token_usage: dict[str, int] = field(default_factory=dict)
    """Accumulated token counts: {"input": N, "output": N}. Populated when available."""

    # Keys from a completed AgentState that carry reusable infra context into
    # the next investigation.  Kept as a class-level tuple so any caller that
    # wants to know "what counts as accumulated context" has a single source.
    _ACCUMULATED_KEYS: tuple[str, ...] = (
        "service",
        "pipeline_name",
        "cluster_name",
        "region",
        "environment",
    )

    def record(self, kind: str, text: str, *, ok: bool = True) -> None:
        """Append an entry to the session history."""
        self.history.append({"type": kind, "text": text, "ok": ok})

    def accumulate_from_state(self, state: dict[str, Any] | None) -> None:
        """Extract reusable infra hints from a completed investigation state.

        Called after every successful investigation (whether triggered by
        free-text input or by the ``/investigate`` slash command) so that
        subsequent investigations within the same REPL session inherit the
        service / cluster / region context discovered earlier.
        """
        if not state:
            return
        for key in self._ACCUMULATED_KEYS:
            value = state.get(key)
            if value:
                self.accumulated_context[key] = value

    def clear(self) -> None:
        """Reset the session to a fresh state (used by /reset)."""
        self.history.clear()
        self.last_state = None
        self.accumulated_context.clear()
        self.token_usage.clear()
        # trust_mode is intentionally preserved across /reset
