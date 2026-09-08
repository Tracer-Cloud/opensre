"""Terminal presentation for the interactive shell agent prompt.

This module owns the **UI / presentation** side of one submitted shell prompt:
the pure presentation-state reducer, the effectful terminal transition renderer,
and the ``ConsoleAgentEventSink`` imperative shell that wires them together.

Keeping this separate from ``runtime/shell_turn_execution.py`` isolates spinner
lifecycle, prompt suppression, interruption/error messages, and stale CPR
draining from the turn's action-routing and prompt-construction logic.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from rich.markup import escape
from rich.text import Text

from config.constants import SOUND_MIN_TURN_SECONDS
from core.llm.shared.llm_retry import OpenSRECreditsExhaustedError
from infrastructure.terminal.notify import NotifyEvent, play_notification
from surfaces.interactive_shell.runtime.core.state import SpinnerState
from surfaces.interactive_shell.runtime.input_policy import turn_should_show_spinner
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import (
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
)
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole
from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes
from surfaces.shared.terminal.components.rendering import hyperlink


@dataclass(frozen=True)
class AgentEvent:
    """Agent lifecycle event emitted during one submitted shell turn."""

    type: Literal["turn_start", "turn_interrupted", "turn_error", "turn_end"]
    text: str | None = None
    error: Exception | None = None


AgentEventSink = Callable[[AgentEvent], Awaitable[None]]


@dataclass(frozen=True)
class AgentPresentationState:
    """Immutable presentation state evolved across lifecycle events."""

    show_spinner: bool = False
    prompt_suppressed: bool = False


def _reduce_agent_presentation(
    state: AgentPresentationState,
    event: AgentEvent,
    *,
    should_show_spinner: bool,
) -> AgentPresentationState:
    """Compute the next presentation state for *event* (pure)."""
    if event.type == "turn_start":
        return AgentPresentationState(
            show_spinner=should_show_spinner,
            prompt_suppressed=should_show_spinner,
        )
    if event.type == "turn_end":
        return AgentPresentationState()
    if event.type in {"turn_interrupted", "turn_error"}:
        return state
    raise ValueError(f"Unknown agent event type: {event.type!r}")


# The exception text carries the destination for surfaces that print plain
# text; the shell shows it once, as a link, on its own line.
_UPGRADE_SENTENCE_LEAD = " Upgrade or top up at"


def _render_credits_exhausted(console: StreamingConsole, exc: Exception) -> None:
    """One error line, then the way out: a clickable top-up link or ``/model``."""
    message = str(exc).split(_UPGRADE_SENTENCE_LEAD, 1)[0].strip()
    console.print(f"[{ERROR}]turn error:[/] {escape(message)}")
    hint = Text("Top up or upgrade: ", style=str(DIM))
    url = getattr(exc, "upgrade_url", None)
    if isinstance(url, str) and url:
        hint.append_text(hyperlink(url, style=f"underline {HIGHLIGHT}"))
    else:
        hint.append("the OpenSRE usage page", style=str(DIM))
    hint.append(" · /account usage opens it · /model switches provider", style=str(DIM))
    console.print(hint)


async def _render_agent_presentation_transition(
    *,
    previous: AgentPresentationState,
    current: AgentPresentationState,
    event: AgentEvent,
    console: StreamingConsole,
    spinner: SpinnerState,
) -> None:
    """Perform the terminal side effects for one presentation transition."""
    match event.type:
        case "turn_start":
            if current.show_spinner:
                spinner.start()
        case "turn_interrupted":
            console.print(f"[{WARNING}]· interrupted[/]")
        case "turn_error":
            exc = event.error
            if exc is None:
                raise ValueError("turn_error event requires an error")
            # On a credit/billing wall, add the in-tool recovery hint.
            from core.llm.shared.llm_retry import LLMCreditExhaustedError

            if isinstance(exc, OpenSRECreditsExhaustedError):
                _render_credits_exhausted(console, exc)
                return
            console.print(f"[{ERROR}]turn error:[/] {escape(str(exc))}")
            if isinstance(exc, LLMCreditExhaustedError):
                console.print(f"[{DIM}]Run /model to switch to another provider.[/]")
                console.print(
                    f"[{DIM}]Or run /auth login <provider> to re-authenticate "
                    f"or add a different provider.[/]"
                )
        case "turn_end":
            if previous.show_spinner:
                spinner.stop()
            await asyncio.sleep(0.05)
            drain_stale_cpr_bytes()
        case _:
            raise ValueError(f"Unknown agent event type: {event.type!r}")


class ConsoleAgentEventSink:
    """Render agent lifecycle events to the terminal console.

    Imperative shell: it holds the evolving ``AgentPresentationState`` and routes
    each event through the pure ``_reduce_agent_presentation`` reducer and the
    effectful ``_render_agent_presentation_transition`` renderer.
    """

    def __init__(
        self,
        *,
        session: Session,
        spinner: SpinnerState,
        console: StreamingConsole,
    ) -> None:
        self.session = session
        self.spinner = spinner
        self.console = console
        self.state = AgentPresentationState()
        self._turn_started_at: float | None = None

    async def __call__(self, event: AgentEvent) -> None:
        if event.type == "turn_start":
            self._turn_started_at = time.monotonic()
        previous = self.state
        self.state = _reduce_agent_presentation(
            previous,
            event,
            should_show_spinner=turn_should_show_spinner(event.text or "", self.session),
        )
        await _render_agent_presentation_transition(
            previous=previous,
            current=self.state,
            event=event,
            console=self.console,
            spinner=self.spinner,
        )
        if event.type in {"turn_end", "turn_interrupted", "turn_error"}:
            self._chime_if_long_turn()

    def _chime_if_long_turn(self) -> None:
        """Chime once a walk-away-length turn finishes; stay silent for quick ones."""
        started = self._turn_started_at
        self._turn_started_at = None
        if started is not None and time.monotonic() - started >= SOUND_MIN_TURN_SECONDS:
            play_notification(NotifyEvent.TURN_COMPLETE)


__all__ = [
    "AgentEvent",
    "AgentPresentationState",
    "ConsoleAgentEventSink",
    "AgentEventSink",
]
