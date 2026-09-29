"""Pure input-action decisions for the interactive shell controller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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


def _is_goal_pause_control(text: str) -> bool:
    """Return whether ``text`` is the exact literal ``/goal pause`` control."""
    return text.lower().split() == ["/goal", "pause"]


@dataclass(frozen=True)
class ShellInputSnapshot:
    exit_requested: bool
    dispatch_running: bool
    awaiting_confirmation: bool


@dataclass(frozen=True)
class IgnoreInput:
    pass


@dataclass(frozen=True)
class CloseShell:
    pass


@dataclass(frozen=True)
class CancelTurn:
    submitted_text: str | None = None


@dataclass(frozen=True)
class PauseGoal:
    submitted_text: str


@dataclass(frozen=True)
class DeliverConfirmation:
    text: str


@dataclass(frozen=True)
class SubmitTurn:
    text: str
    wait_until_idle: bool = False
    warning: str | None = None


InputAction = IgnoreInput | CloseShell | CancelTurn | PauseGoal | DeliverConfirmation | SubmitTurn


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
            return CancelTurn()
        case InputSubmitted(text):
            if snapshot.exit_requested or not text:
                return IgnoreInput()

            # Drop pasted ``[n] ❯`` prompt chrome so it never becomes the user
            # turn, SessionGoal condition, or a doubled ``[n] ❯ [n] ❯`` echo.
            stripped = strip_shell_prompt_chrome(text)
            if not stripped:
                return IgnoreInput()

            if snapshot.dispatch_running and looks_like_cancel_request(stripped):
                return CancelTurn(submitted_text=stripped)

            if snapshot.dispatch_running and _is_goal_pause_control(stripped):
                return PauseGoal(submitted_text=stripped)

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
    "CancelTurn",
    "CloseShell",
    "DeliverConfirmation",
    "IgnoreInput",
    "InputAction",
    "PauseGoal",
    "QUEUE_DURING_CONFIRMATION_WARNING",
    "ShellInputSnapshot",
    "SubmitTurn",
    "decide_input_action",
]
