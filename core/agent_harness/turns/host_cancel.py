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

Scheduled ticks write that same Event when the stored task is disabled or
removed (:class:`PredicateCancelConsole`). Do not invent a second cancel
channel.

The shell sink shares its ``StreamingConsole.cancel_event`` as ``turn_cancel``
so cancelling the UI and the worker signals the same turn.
"""

from __future__ import annotations

import contextlib
import enum
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY


class HostCancelReason(enum.StrEnum):
    """Why the host requested cooperative cancellation of a turn."""

    STOP = "stop"
    GOAL_PAUSE = "goal_pause"
    GOAL_CLEAR = "goal_clear"


def _goal_control_priority(reason: HostCancelReason | None) -> int:
    """Return goal-control precedence, or zero for ordinary cancellation."""
    if reason is HostCancelReason.GOAL_PAUSE:
        return 1
    if reason is HostCancelReason.GOAL_CLEAR:
        return 2
    return 0


def is_goal_control_reason(reason: HostCancelReason | None) -> bool:
    """Return whether ``reason`` requires a safe session-goal boundary."""
    return _goal_control_priority(reason) > 0


class HostCancelEvent(threading.Event):
    """The canonical turn-cancel event plus its host-supplied reason."""

    def __init__(self) -> None:
        super().__init__()
        self._reason_lock = threading.Lock()
        self._reason: HostCancelReason | None = None

    @property
    def reason(self) -> HostCancelReason | None:
        """Return the effective reason recorded for this turn."""
        with self._reason_lock:
            return self._reason

    def request(self, reason: HostCancelReason, *, interrupt: bool = True) -> None:
        """Record ``reason`` and optionally wake cooperative cancel readers."""
        with self._reason_lock:
            # Goal controls mutate post-turn state as well as interrupting the
            # current turn. Preserve them across a later generic stop, and let
            # a destructive clear supersede an earlier pause.
            current_priority = _goal_control_priority(self._reason)
            requested_priority = _goal_control_priority(reason)
            if requested_priority >= current_priority:
                self._reason = reason
            if interrupt:
                super().set()

    def set(self) -> None:
        """Request an ordinary host stop."""
        self.request(HostCancelReason.STOP)

    def clear(self) -> None:
        """Reset both the event and its recorded reason."""
        with self._reason_lock:
            super().clear()
            self._reason = None


def ensure_turn_cancel(output: Any) -> threading.Event:
    """Return the turn's cancel Event on ``output``, creating one when missing.

    Prefer attaching before the turn starts (chat transports). When the sink
    rejects dynamic attributes, callers still hold the returned Event for a
    console wrapper that exposes ``cancel_requested``.
    """
    existing = getattr(output, "turn_cancel", None)
    if isinstance(existing, threading.Event):
        return existing
    event = HostCancelEvent()
    with contextlib.suppress(Exception):
        output.turn_cancel = event
    return event


def host_cancel_requested(output: Any | None) -> bool:
    """True when the bound sink's ``turn_cancel`` Event is set."""
    if output is None:
        return False
    cancel = getattr(output, "turn_cancel", None)
    return isinstance(cancel, threading.Event) and cancel.is_set()


def turn_cancel_reason(cancel: threading.Event | None) -> HostCancelReason | None:
    """Return the reason carried by the canonical cancel event, when available."""
    if isinstance(cancel, HostCancelEvent):
        return cancel.reason
    return None


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


_PREDICATE_OWN_ATTRIBUTES = frozenset({"_inner", "_event", "_is_cancelled"})


class PredicateCancelConsole:
    """Console that writes the host cancel Event when ``is_cancelled`` is true.

    Readers still use ``event`` / ``cancel_requested`` — this is a writer, not
    a second channel.
    """

    def __init__(
        self,
        inner: Any,
        event: threading.Event,
        is_cancelled: Callable[[], bool],
    ) -> None:
        self._inner = inner
        self._event = event
        self._is_cancelled = is_cancelled

    @property
    def cancel_requested(self) -> bool:
        if not self._event.is_set() and self._is_cancelled():
            self._event.set()
        return self._event.is_set()

    def print(self, *args: Any, **kwargs: Any) -> None:
        self._inner.print(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _PREDICATE_OWN_ATTRIBUTES:
            super().__setattr__(name, value)
            return
        setattr(self._inner, name, value)


def bind_cancel_predicate(
    output: Any,
    is_cancelled: Callable[[], bool],
    *,
    console: Any | None = None,
) -> PredicateCancelConsole:
    """Attach the host cancel Event and a console that writes it from ``is_cancelled``."""
    event = ensure_turn_cancel(output)
    inner = console if console is not None else CancelProbeConsole(is_cancelled=is_cancelled)
    return PredicateCancelConsole(inner, event, is_cancelled)


__all__ = [
    "CancelProbeConsole",
    "CancelProbeContext",
    "HostCancelEvent",
    "HostCancelReason",
    "PredicateCancelConsole",
    "bind_cancel_predicate",
    "cancel_tool_resources",
    "ensure_turn_cancel",
    "host_cancel_requested",
    "is_goal_control_reason",
    "turn_cancel_reason",
]
