from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ProgressEvent:
    node_name: str
    elapsed_ms: int
    fields_updated: list[str] = field(default_factory=list)
    status: str = "completed"
    message: str | None = None


@runtime_checkable
class DisplayProtocol(Protocol):
    """Shared interface for :class:`_EventLogDisplay` and :class:`_ReplEventLogDisplay`.

    Lets :class:`tracker.ProgressTracker` hold either display type without
    ``isinstance`` branching on concrete classes.
    """

    def stop(self) -> None:
        raise NotImplementedError

    def step_start(self, node_name: str) -> None:
        raise NotImplementedError

    def step_complete(self, node_name: str, event: ProgressEvent) -> None:
        raise NotImplementedError

    def step_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        raise NotImplementedError

    def set_tool_details(
        self,
        *,
        visible: bool,
        records: list[dict[str, Any]],
        summary: str,
        clear: bool = False,
    ) -> None:
        raise NotImplementedError

    def print_above(self, text: str) -> None:
        raise NotImplementedError

    def print_above_renderable(self, renderable: Any) -> None:
        raise NotImplementedError
