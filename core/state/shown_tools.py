"""The tools a conversation has been shown, kept across its turns."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from core.state.turn_evidence import TurnEvidence


@dataclass
class ShownTools:
    """The tool list a conversation was last sent, in the order it first saw each tool.

    Tool definitions open the request prefix the provider caches, so a tool
    inserted ahead of another, a description rewritten in place, or a list that
    shrinks back between turns re-prices everything after it. A catalog keeps
    each tool where and how the conversation first saw it and adds new tools at
    the end, so the list only ever grows at its tail.
    """

    #: Tool names in the order the conversation first saw them.
    names: list[str] = field(default_factory=list)
    #: Names first shown with their full description rather than the compact one.
    full: set[str] = field(default_factory=set)

    def clear(self) -> None:
        """Forget every tool, for a conversation that starts over."""
        self.names.clear()
        self.full.clear()

    @contextmanager
    def unchanged(self) -> Iterator[None]:
        """Let a preview build the tool list without changing what the conversation was shown."""
        names, full = list(self.names), set(self.full)
        try:
            yield
        finally:
            self.names[:] = names
            self.full.clear()
            self.full.update(full)

    def restore_from(self, evidence: Sequence[TurnEvidence]) -> None:
        """Take the list the newest recorded turn ended with, for a session rebuilt from its log."""
        latest = next((record for record in reversed(evidence) if record.shown_tools), None)
        if latest is None:
            return
        self.names[:] = latest.shown_tools
        self.full.clear()
        self.full.update(name for name in latest.full_tools if name in self.names)


__all__ = ["ShownTools"]
