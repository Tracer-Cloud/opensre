"""Everything one turn shows the person who asked.

Progress lines, streamed answer text, the live tool status, and the final
answer all go out through here. A channel implements it; the host only writes
to it, and never reads back.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.agent_harness import OutputSink


@runtime_checkable
class TurnOutput(OutputSink, Protocol):
    def set_tool_status(self, status: str) -> None:
        """Show live tool progress for the running turn."""

    def finalize(self, answer: str) -> None:
        """Deliver the turn's final answer to the chat."""
