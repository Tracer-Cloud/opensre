"""Host cancel signal for chat turns — one Event, many readers.

Mental model (do not invent a second cancel channel)::

    transport soft-timeout / user ``/stop``
            │
            ▼
    sink.turn_cancel  (threading.Event)   ← sole write site
            │
            ├── console.cancel_requested        → ReAct + tools
            ├── host_cancel_requested(output)   → orchestrator / gather
            └── output stream guard             → stop draining LLM chunks

:func:`ensure_turn_cancel` is the create/attach seam. Chat transports set the
Event on the concrete sink before the turn; retargetable output adapters
forward ``turn_cancel`` so the harness output port and the transport share
one signal.

The shell sink shares its ``StreamingConsole.cancel_event`` as ``turn_cancel``
so cancelling the UI and the worker signals the same turn.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY


def ensure_turn_cancel(output: Any) -> threading.Event:
    """Return the turn's cancel Event on ``output``, creating one when missing.

    Prefer attaching before the turn starts (chat transports). When the sink
    rejects dynamic attributes, callers still hold the returned Event for a
    console wrapper that exposes ``cancel_requested``.
    """
    existing = getattr(output, "turn_cancel", None)
    if isinstance(existing, threading.Event):
        return existing
    event = threading.Event()
    with contextlib.suppress(Exception):
        output.turn_cancel = event
    return event


def host_cancel_requested(output: Any | None) -> bool:
    """True when the bound sink's ``turn_cancel`` Event is set."""
    if output is None:
        return False
    cancel = getattr(output, "turn_cancel", None)
    return isinstance(cancel, threading.Event) and cancel.is_set()


@dataclass(frozen=True, slots=True)
class CancelProbeConsole:
    """Minimal console so gather ReAct sees the same ``cancel_requested`` flag."""

    is_cancelled: Callable[[], bool]

    @property
    def cancel_requested(self) -> bool:
        return bool(self.is_cancelled())

    def print(self, *args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)


@dataclass(frozen=True, slots=True)
class CancelProbeContext:
    """Stand-in ``ActionToolScope`` carrying only the cancel console."""

    console: CancelProbeConsole


def cancel_tool_resources(is_cancelled: Callable[[], bool] | None) -> dict[str, Any]:
    """``tool_resources`` so :class:`~core.agent.react_loop.ReactLoop` sees cancel."""
    if is_cancelled is None:
        return {}
    return {
        ACTION_TOOL_CONTEXT_RESOURCE_KEY: CancelProbeContext(
            console=CancelProbeConsole(is_cancelled=is_cancelled)
        )
    }


__all__ = [
    "CancelProbeConsole",
    "CancelProbeContext",
    "cancel_tool_resources",
    "ensure_turn_cancel",
    "host_cancel_requested",
]
