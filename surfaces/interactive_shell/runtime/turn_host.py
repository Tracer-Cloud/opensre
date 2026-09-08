"""Runtime turn host for submitted interactive-shell prompts.

Three public runtime functions live here:

- ``run_agent_turn`` — set up shell presentation for one submitted turn and drive
  its lifecycle (the injected ``run_turn`` callable for the queue).
- ``run_input_loop`` — read prompt input events and dispatch them until exit.
- ``run_agent_turn_queue`` — consume queued turns and run each one until exit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.console import Console

if TYPE_CHECKING:
    from infrastructure.turn_host.turn_runner import TurnRunner

from core.llm.shared.llm_retry import OpenSRECreditsExhaustedError
from infrastructure.analytics.repl_context import bound_repl_turn_context
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.observability.trace.spans import (
    bind_session_trace,
    emit_thread_boundary,
)
from surfaces.interactive_shell.runtime.agent_presentation import (
    AgentEvent,
    AgentEventSink,
    ConsoleAgentEventSink,
)
from surfaces.interactive_shell.runtime.background.workers import (
    BackgroundTaskPool,
)
from surfaces.interactive_shell.runtime.core.confirm_keys import read_confirm_answer
from surfaces.interactive_shell.runtime.core.confirmation import (
    DispatchCancelled,
    request_confirmation_via_prompt,
)
from surfaces.interactive_shell.runtime.core.state import (
    DEFAULT_CONFIRM_OPTIONS,
    ReplState,
    SpinnerState,
)
from surfaces.interactive_shell.runtime.credit_wall import queue_credits_exhausted_menu
from surfaces.interactive_shell.runtime.input import PromptInputReader
from surfaces.interactive_shell.runtime.input.actions import (
    InputAction,
    ShellInputSnapshot,
    decide_input_action,
)
from surfaces.interactive_shell.runtime.input_policy import (
    turn_needs_exclusive_stdin,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry import PromptRecorder
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole
from surfaces.shared.error_handling.exception_reporting import report_exception
from surfaces.shared.terminal.output.console_state import set_turn_spinner
from surfaces.shared.terminal.output.repl_progress import repl_safe_progress_scope

_logger = logging.getLogger(__name__)

_AGENT_TURN_KIND = "agent"


@dataclass(frozen=True)
class AgentTurnResources:
    """Immutable dependencies for running one submitted shell turn."""

    session: Session
    state: ReplState
    spinner: SpinnerState
    invalidate_prompt: Callable[[], None]
    request_exit: Callable[[], None] | None = None
    #: Where this turn's streamed output goes. ``None`` keeps the shell's own
    #: terminal; an embedding caller passes its console so agent responses and
    #: tool output land in the same stream as the startup renders.
    console: Console | None = None
    #: Session-scoped turn host; each turn binds its own streaming console.
    turn_handler: TurnRunner | None = None


def _confirm_via_prompt(runtime: AgentTurnResources, prompt: str) -> str:
    """Park for a y/n answer; hide the free-text box via ReplState confirmation phase.

    ``begin_confirmation`` flips ``state.is_awaiting_confirmation()``, which
    ``typing_box_hidden`` / ``render_prompt_region`` already honor. ``redraw``
    invalidates the live prompt immediately so the box hides and restores
    without waiting for the next refresh tick. ``prepare_ui`` clears any
    typeahead that landed in the (hidden) composer before the gate opened.
    """
    # The execution gate stashes the rows it wants (e.g. an "always allow" row)
    # on the terminal just before calling this; consume them for the choice.
    terminal = runtime.session.terminal
    options = terminal.pending_confirm_options
    terminal.pending_confirm_options = None
    app = terminal.prompt_app
    prompt_running = app is not None and getattr(app, "is_running", False)
    if terminal.exclusive_stdin_active or not prompt_running:
        # Exclusive-stdin / subprocess turns own the TTY (and ``is_running`` can
        # still be true). Parking on the prompt app hangs while the cooked
        # terminal echoes the arrow keys, so read a plain line instead.
        return _confirm_via_readline(prompt, options)
    return request_confirmation_via_prompt(
        runtime.state,
        prompt,
        options=options,
        redraw=runtime.invalidate_prompt,
        prepare_ui=lambda: _reset_prompt_buffer(runtime.session),
    )


def _confirm_via_readline(prompt: str, options: tuple[tuple[str, str], ...] | None) -> str:
    """Confirmation for when the arrow-nav prompt app is unavailable.

    On a TTY this reads one keypress in cbreak mode — echo off, so arrow keys no
    longer leak as raw ``^[[A`` — resolving a row tag, digit, answer key, or
    Enter (cancel); off a TTY it falls back to a cooked one-line read.
    """
    rows = options or DEFAULT_CONFIRM_OPTIONS
    return read_confirm_answer(prompt, rows)


def _reset_prompt_buffer(session: Session) -> None:
    """Empty the live prompt buffer so hidden typeahead cannot submit later.

    Confirmation parks on a worker thread; prompt-toolkit buffer mutations must
    run on the UI loop (same rule as ``invalidate_prompt`` / refresh prefill).
    """
    terminal = getattr(session, "terminal", None)
    if terminal is None:
        return
    app = getattr(terminal, "prompt_app", None)
    if app is None:
        return

    def _reset() -> None:
        buffer = getattr(app, "current_buffer", None)
        if buffer is not None:
            buffer.reset()

    loop = getattr(terminal, "main_loop", None)
    if loop is not None:
        loop.call_soon_threadsafe(_reset)
        return
    _reset()


def _streaming_console(
    runtime: AgentTurnResources, cancel_event: threading.Event
) -> StreamingConsole:
    """Spinner-aware console for one turn, writing where the caller asked.

    The turn needs a :class:`StreamingConsole` for progress and cancellation, so
    an injected console cannot be used directly. It renders *through* that
    console rather than to a copy of its file, so a caller's ``capture()`` and
    ``record`` see the turn.
    """
    base = runtime.console
    if base is None:
        return StreamingConsole(
            runtime.spinner,
            cancel_event,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
    return StreamingConsole(
        runtime.spinner,
        cancel_event,
        output=base,
        highlight=False,
        force_terminal=base.is_terminal,
    )


async def run_agent_turn(runtime: AgentTurnResources, text: str) -> None:
    """Set up shell presentation for one turn and drive its lifecycle."""
    dispatch_cancel = threading.Event()
    console = _streaming_console(runtime, dispatch_cancel)
    emit = ConsoleAgentEventSink(
        session=runtime.session,
        spinner=runtime.spinner,
        console=console,
    )
    recorder = PromptRecorder.start(
        session=runtime.session,
        text=text,
        turn_kind=_AGENT_TURN_KIND,
    )
    exclusive_stdin = turn_needs_exclusive_stdin(text, runtime.session)
    progress_scope = contextlib.nullcontext() if exclusive_stdin else repl_safe_progress_scope()
    runtime.session.terminal.exclusive_stdin_active = exclusive_stdin
    # Blocks nested validate_and_handle from set_auto_command (e.g. /goal set).
    runtime.session.terminal.dispatch_active = True
    # Expose this turn's spinner so rendering helpers can animate phase labels.
    set_turn_spinner(runtime.spinner)
    emit_thread_boundary(
        runtime.session.session_id,
        name="turn_boundary",
        phase="turn_start",
    )
    try:
        with (
            bind_session_trace(runtime.session.session_id),
            progress_scope,
        ):
            await _run_agent_turn_loop(
                runtime=runtime,
                text=text,
                output=console,
                recorder=recorder,
                confirm=lambda prompt: _confirm_via_prompt(runtime, prompt),
                emit=emit,
                dispatch_cancel=dispatch_cancel,
            )
    finally:
        set_turn_spinner(None)
        runtime.session.terminal.exclusive_stdin_active = False
        runtime.session.terminal.dispatch_active = False
        # ``set_auto_command`` deliberately avoids submitting while a turn is
        # active. If the input prompt was already open, wake it again now that
        # the turn is idle so deferred commands such as ``/choose`` can run.
        if (
            runtime.session.terminal.pending_prompt_default
            and runtime.session.terminal.pending_prompt_autosubmit
        ):
            runtime.session.terminal.notify_prompt_changed()
        emit_thread_boundary(
            runtime.session.session_id,
            name="turn_boundary",
            phase="turn_end",
        )


async def _run_agent_turn_loop(
    *,
    runtime: AgentTurnResources,
    text: str,
    output: StreamingConsole,
    recorder: PromptRecorder | None,
    confirm: Callable[[str], str],
    emit: AgentEventSink,
    dispatch_cancel: threading.Event,
) -> None:
    current_task = asyncio.current_task()
    if current_task is not None:
        runtime.state.start_dispatch(task=current_task, cancel_event=dispatch_cancel)
    else:
        runtime.state.attach_cancel_event(dispatch_cancel)

    await emit(AgentEvent(type="turn_start", text=text))
    # Repaint the prompt now so the spinner shows the turn is in flight
    # immediately, instead of waiting for the ticker's next 100 ms tick.
    runtime.invalidate_prompt()
    try:
        # Imported lazily so constructing the controller (and importing this
        # module) does not pull the harness/turn-execution stack
        # (``action_agent -> core.agent``) before the first turn is queued.
        from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn

        with (
            bound_usage_context(
                surface=UsageSurface.CLI,
                session_id=runtime.session.session_id,
            ),
            bound_repl_turn_context(
                session_id=runtime.session.session_id,
                turn_kind=_AGENT_TURN_KIND,
                prompt_turn_id=recorder.turn_id if recorder is not None else None,
            ),
        ):
            await asyncio.to_thread(
                execute_shell_turn,
                text,
                runtime.session,
                output,
                recorder=recorder,
                confirm_fn=confirm,
                is_tty=None,
                request_exit=runtime.request_exit,
                handler=runtime.turn_handler,
            )
    except asyncio.CancelledError:
        await emit(AgentEvent(type="turn_interrupted"))
        raise
    except DispatchCancelled:
        await emit(AgentEvent(type="turn_interrupted"))
    except Exception as exc:
        report_exception(exc, context="surfaces.interactive_shell.turn")
        if isinstance(exc, OpenSRECreditsExhaustedError):
            queue_credits_exhausted_menu(runtime.session)
        await emit(AgentEvent(type="turn_error", error=exc))
    finally:
        runtime.state.finish_dispatch(dispatch_cancel)
        await emit(AgentEvent(type="turn_end"))


async def run_input_loop(
    *,
    state: ReplState,
    session: Session,
    background: BackgroundTaskPool | None,
    input_reader: PromptInputReader,
    echo_console: Console,
    handle_input_action: Callable[[InputAction], Awaitable[bool]],
) -> None:
    """Run the interactive session's main input loop until exit or close.

    This loop reads input; it does not run agent turns itself. Each raw input
    event is classified into an ``InputAction`` by ``decide_input_action`` and
    handed to ``handle_input_action``. For a submitted prompt that handler pushes
    the text onto ``state.queue``; the queued text is then consumed
    asynchronously by ``run_agent_turn_queue`` (started in the controller's
    ``_start_runtime_services``), which runs each turn via ``run_agent_turn``.

    Keeping input reading and turn execution as two separate loops joined only by
    ``state.queue`` is deliberate: it lets the user keep typing, cancel, or
    answer a confirmation while a turn is still in flight.
    """
    while not state.exit_requested:
        if background is not None:
            background.drain_turn_start_output(echo_console)
        event = await input_reader.read()
        action = decide_input_action(
            event,
            ShellInputSnapshot(
                exit_requested=state.exit_requested,
                dispatch_running=state.is_dispatch_running(),
                awaiting_confirmation=state.is_awaiting_confirmation(),
            ),
            needs_exclusive_stdin=lambda text: turn_needs_exclusive_stdin(
                text,
                session,
            ),
        )
        should_continue = await handle_input_action(action)
        if not should_continue:
            return


async def run_agent_turn_queue(
    *,
    state: ReplState,
    run_turn: Callable[[str], Coroutine[Any, Any, None]],
) -> None:
    """Consume queued turns and run each one until exit."""
    while not state.exit_requested:
        try:
            text = await state.queue.get()
        except asyncio.CancelledError:
            return
        if state.exit_requested:
            state.queue.task_done()
            return

        turn_task = asyncio.create_task(run_turn(text))
        state.attach_turn_task(turn_task)
        try:
            await turn_task
        except asyncio.CancelledError:
            _logger.debug("Queued turn task was cancelled")
        except Exception as exc:
            _logger.debug("Queued turn task ended with exception: %s", exc)
        finally:
            state.clear_current_task()
            state.queue.task_done()


__all__ = [
    "AgentTurnResources",
    "run_agent_turn",
    "run_agent_turn_queue",
    "run_input_loop",
]
