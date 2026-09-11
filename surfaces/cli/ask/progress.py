"""Terminal-only activity display for interactive ``opensre ask`` runs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from core.agent_harness.ports import ToolEventObserver
from infrastructure.safety.terminal_output import strip_terminal_controls

_INITIAL_STATUS = "Investigating…"
_ANALYZING_STATUS = "Analyzing results…"


def status_for_tool_event(kind: str, data: dict[str, Any]) -> str | None:
    """Return safe status copy for a tool lifecycle event."""
    if kind == "tool_end":
        return _ANALYZING_STATUS
    if kind != "tool_start":
        return None
    tool_name = " ".join(strip_terminal_controls(str(data.get("name") or "")).split())
    if not tool_name:
        return None
    return f"Running {tool_name.replace('_', ' ')}…"


class AskProgress:
    """Render one ephemeral spinner and update it as the agent starts tools."""

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}", markup=False),
            TimeElapsedColumn(),
            console=Console(stderr=True, highlight=False),
            refresh_per_second=12,
            transient=True,
        )
        self._task_id = self._progress.add_task(_INITIAL_STATUS, total=None)

    def __enter__(self) -> AskProgress:
        self._progress.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._progress.stop()

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        status = status_for_tool_event(kind, data)
        if status is not None:
            self._progress.update(self._task_id, description=status)


@contextmanager
def ask_progress_scope(*, enabled: bool) -> Iterator[ToolEventObserver | None]:
    """Yield a live tool observer only for an interactive terminal invocation."""
    if not enabled:
        yield None
        return
    with AskProgress() as progress:
        yield progress


__all__ = ["AskProgress", "ask_progress_scope", "status_for_tool_event"]
