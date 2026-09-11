"""Terminal-only activity display for interactive ``opensre ask`` runs."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from rich.console import Console
from rich.progress import Progress, ProgressColumn, Task, TextColumn
from rich.table import Column
from rich.text import Text

from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal.spinner_frames import spinner_frames
from infrastructure.terminal.theme import BRAND, DIM, HIGHLIGHT, TEXT

_THINKING_STATUS = "Thinking…"
_FRAME_INTERVAL_SECONDS = 0.1

ToolEventObserver = Callable[[str, dict[str, Any]], None]


class _ActivitySpinnerColumn(ProgressColumn):
    """Render the shared one-cell spinner in the appropriate activity color."""

    def __init__(self) -> None:
        super().__init__()
        self._frames = spinner_frames()

    def render(self, task: Task) -> Text:
        elapsed = task.elapsed or 0.0
        frame = self._frames[int(elapsed / _FRAME_INTERVAL_SECONDS) % len(self._frames)]
        style = BRAND if task.fields.get("tool_active", False) else HIGHLIGHT
        return Text(frame, style=style)


class _ElapsedColumn(ProgressColumn):
    """Render elapsed time with the shared muted terminal token."""

    def render(self, task: Task) -> Text:
        elapsed = task.elapsed or 0.0
        return Text(str(timedelta(seconds=max(0, int(elapsed)))), style=DIM)


def _status_for_tool_start(data: dict[str, Any]) -> str | None:
    """Describe a requested tool batch without overstating execution order."""
    tool_call_count = data.get("tool_call_count")
    if (
        isinstance(tool_call_count, int)
        and not isinstance(tool_call_count, bool)
        and tool_call_count > 1
    ):
        if data.get("tool_call_index") != 0:
            return None
        return f"Invoking {tool_call_count} tools…"

    tool_name = " ".join(strip_terminal_controls(str(data.get("name") or "")).split())
    if not tool_name:
        return None
    return f"Invoking tools… · {tool_name.replace('_', ' ')}"


def status_for_tool_event(kind: str, data: dict[str, Any]) -> str | None:
    """Return safe status copy for a tool lifecycle event."""
    if kind == "tool_end":
        return _THINKING_STATUS
    if kind != "tool_start":
        return None
    return _status_for_tool_start(data)


class AskProgress:
    """Render one ephemeral spinner and update it as the agent starts tools."""

    def __init__(self) -> None:
        self._console = Console(stderr=True, highlight=False)
        self._progress = Progress(
            _ActivitySpinnerColumn(),
            TextColumn(
                "{task.description}",
                markup=False,
                style=TEXT,
                table_column=Column(no_wrap=True, overflow="ellipsis"),
            ),
            _ElapsedColumn(),
            console=self._console,
            refresh_per_second=12,
            transient=True,
        )
        self._task_id = self._progress.add_task(_THINKING_STATUS, total=None, tool_active=False)

    def __enter__(self) -> AskProgress:
        self._console.print()
        self._progress.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._progress.stop()

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        status = status_for_tool_event(kind, data)
        if status is not None:
            self._progress.update(
                self._task_id,
                description=status,
                tool_active=kind == "tool_start",
            )


@contextmanager
def ask_progress_scope(*, enabled: bool) -> Iterator[ToolEventObserver | None]:
    """Yield a live tool observer only for an interactive terminal invocation."""
    if not enabled:
        yield None
        return
    with AskProgress() as progress:
        yield progress


__all__ = ["AskProgress", "ask_progress_scope", "status_for_tool_event"]
