"""Pure input-action decisions for the interactive shell controller."""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass

from core.agent_harness.spi.cancel import HostCancelReason
from core.agent_harness.spi.prompt_chrome import strip_shell_prompt_chrome
from surfaces.interactive_shell.runtime.core.turn_detection import (
    looks_like_cancel_request,
    looks_like_confirmation_answer,
)
from surfaces.interactive_shell.runtime.input.events import (
    InputCancelled,
    InputClosed,
    InputEvent,
    InputSubmitted,
)

QUEUE_DURING_CONFIRMATION_WARNING = (
    "[dim](type y/N to confirm the pending action; your input has been queued for after)[/]"
)


class InflightControl(enum.StrEnum):
    """Exact literal controls that may act while a dispatch owns the worker."""

    CANCEL_TURN = "cancel_turn"
    PAUSE_GOAL = "pause_goal"
    CLEAR_GOAL = "clear_goal"
    EXIT_SHELL = "exit_shell"


def goal_control_reason(control: InflightControl) -> HostCancelReason | None:
    """Return the host reason for an in-flight goal control, if any."""
    if control is InflightControl.PAUSE_GOAL:
        return HostCancelReason.GOAL_PAUSE
    if control is InflightControl.CLEAR_GOAL:
        return HostCancelReason.GOAL_CLEAR
    return None


def _inflight_control(text: str) -> InflightControl | None:
    """Resolve an exact literal control without inferring natural-language intent."""
    if looks_like_cancel_request(text):
        return InflightControl.CANCEL_TURN
    parts = text.lower().split()
    if parts == ["/goal", "pause"]:
        return InflightControl.PAUSE_GOAL
    if parts in (["/goal", "clear"], ["/goal", "unset"]):
        return InflightControl.CLEAR_GOAL
    if parts in (["/exit"], ["/quit"]):
        return InflightControl.EXIT_SHELL
    return None


@dataclass(frozen=True)
class ShellInputSnapshot:
    exit_requested: bool
    dispatch_running: bool
    awaiting_confirmation: bool
    worker_running: bool = False


@dataclass(frozen=True)
class IgnoreInput:
    pass


@dataclass(frozen=True)
class CloseShell:
    pass


@dataclass(frozen=True)
class RunInflightControl:
    control: InflightControl
    submitted_text: str = ""


@dataclass(frozen=True)
class DeliverConfirmation:
    text: str


@dataclass(frozen=True)
class SubmitTurn:
    text: str
    wait_until_idle: bool = False
    warning: str | None = None


InputAction = IgnoreInput | CloseShell | RunInflightControl | DeliverConfirmation | SubmitTurn


def decide_input_action(
    event: InputEvent,
    snapshot: ShellInputSnapshot,
    *,
    needs_exclusive_stdin: Callable[[str], bool],
) -> InputAction:
    """Interpret one prompt event without mutating runtime state."""
    match event:
        case InputClosed():
            return CloseShell()
        case InputCancelled():
            return RunInflightControl(control=InflightControl.CANCEL_TURN)
        case InputSubmitted(text):
            if snapshot.exit_requested or not text:
                return IgnoreInput()

            # Drop pasted ``[n] ❯`` prompt chrome so it never becomes the user
            # turn, SessionGoal condition, or a doubled ``[n] ❯ [n] ❯`` echo.
            stripped = strip_shell_prompt_chrome(text)
            if not stripped:
                return IgnoreInput()

            control = _inflight_control(stripped)
            if control is not None and (snapshot.dispatch_running or snapshot.worker_running):
                return RunInflightControl(control=control, submitted_text=stripped)

            if snapshot.awaiting_confirmation:
                if looks_like_confirmation_answer(stripped):
                    return DeliverConfirmation(text=stripped)
                return SubmitTurn(
                    text=stripped,
                    warning=QUEUE_DURING_CONFIRMATION_WARNING,
                )

            return SubmitTurn(
                text=stripped,
                wait_until_idle=needs_exclusive_stdin(stripped),
            )
    raise AssertionError(f"Unhandled input event: {event!r}")


__all__ = [
    "CloseShell",
    "DeliverConfirmation",
    "IgnoreInput",
    "InflightControl",
    "InputAction",
    "QUEUE_DURING_CONFIRMATION_WARNING",
    "RunInflightControl",
    "ShellInputSnapshot",
    "SubmitTurn",
    "decide_input_action",
    "goal_control_reason",
]
