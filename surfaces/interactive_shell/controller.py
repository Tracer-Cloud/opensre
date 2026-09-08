"""Top-level interactive-shell controller: prompt input, dispatch, and shutdown."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

from prompt_toolkit import PromptSession
from rich.console import Console

from config.repl_config import ReplConfig
from core.domain.alerts import inbox as _alert_inbox
from surfaces.interactive_shell.runtime.background.workers import BackgroundTaskPool
from surfaces.interactive_shell.runtime.context import (
    ReplRuntime,
    create_repl_runtime,
)
from surfaces.interactive_shell.runtime.core.prompt_builder import PromptBuilder
from surfaces.interactive_shell.runtime.core.state import (
    ReplState,
    SpinnerState,
)
from surfaces.interactive_shell.runtime.input import (
    PromptInputReader,
)
from surfaces.interactive_shell.runtime.input.actions import (
    CancelTurn,
    CloseShell,
    DeliverConfirmation,
    IgnoreInput,
    InputAction,
    SubmitTurn,
)
from surfaces.interactive_shell.runtime.loop_scheduler import (
    shutdown_loop_scheduler,
    start_loop_scheduler,
)
from surfaces.interactive_shell.runtime.turn_host import (
    AgentTurnResources,
    run_agent_turn,
    run_agent_turn_queue,
    run_input_loop,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import DIM
from surfaces.interactive_shell.ui.input_prompt.stdout import patch_prompt_stdout

log = logging.getLogger(__name__)


@contextmanager
def _alert_listener_token(token: str | None) -> Iterator[None]:
    """Install the configured alert token for this listener, restoring any prior value."""
    key = "OPENSRE_ALERT_LISTENER_TOKEN"
    previous = os.environ.get(key)
    try:
        if token:
            os.environ[key] = token
        else:
            os.environ.pop(key, None)
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


@contextmanager
def _alert_listener(
    cfg: ReplConfig | None,
    console: Console,
    *,
    existing: _alert_inbox.AlertInbox | None = None,
) -> Iterator[_alert_inbox.AlertInbox | None]:
    if existing is not None:
        yield existing
        return
    if cfg is None or not cfg.alert_listener_enabled:
        yield None
        return

    from infrastructure.alert_intake import build_alert_intake_app
    from infrastructure.asgi_server import AsgiServerHandle, serve_asgi_in_thread

    inbox: _alert_inbox.AlertInbox | None = None
    handle: AsgiServerHandle | None = None
    try:
        inbox = _alert_inbox.AlertInbox()
        _alert_inbox.set_current_inbox(inbox)
        with _alert_listener_token(cfg.alert_listener_token):
            handle = serve_asgi_in_thread(
                build_alert_intake_app(),
                host=cfg.alert_listener_host,
                port=cfg.alert_listener_port,
            )
            console.print(f"[{DIM}]listening for alerts on http://{handle.bound_address}/alerts[/]")
            try:
                yield inbox
            finally:
                if handle is not None:
                    handle.stop()
                _alert_inbox.set_current_inbox(None)
    except Exception as exc:
        log.warning("Alert listener could not start: %s — continuing without it.", exc)
        _alert_inbox.set_current_inbox(None)
        yield None


def _resolve_runtime_context(
    session: Session | ReplRuntime | None,
    *,
    state: ReplState | None,
    spinner: SpinnerState | None,
    pt_session: PromptSession[str] | None,
    inbox: _alert_inbox.AlertInbox | None,
) -> ReplRuntime:
    if isinstance(session, ReplRuntime):
        if state is None and spinner is None and pt_session is None and inbox is None:
            return session
        return ReplRuntime(
            session=session.session,
            state=state if state is not None else session.state,
            spinner=spinner if spinner is not None else session.spinner,
            pt_session=pt_session if pt_session is not None else session.pt_session,
            inbox=inbox if inbox is not None else session.inbox,
        )
    return create_repl_runtime(
        session,
        state=state,
        spinner=spinner,
        pt_session=pt_session,
        inbox=inbox,
    )


class InteractiveShellController:
    """Coordinate prompt input, queued dispatch, background workers, and shutdown."""

    def __init__(
        self,
        session: Session | ReplRuntime | None = None,
        *,
        config: ReplConfig | None = None,
        state: ReplState | None = None,
        spinner: SpinnerState | None = None,
        pt_session: PromptSession[str] | None = None,
        inbox: _alert_inbox.AlertInbox | None = None,
        console: Console | None = None,
    ) -> None:
        self.runtime_context = _resolve_runtime_context(
            session,
            state=state,
            spinner=spinner,
            pt_session=pt_session,
            inbox=inbox,
        )
        self.config = config
        self.session = self.runtime_context.session
        self.inbox = self.runtime_context.inbox
        self.state = self.runtime_context.state
        self.spinner = self.runtime_context.spinner
        self.service_console = console or Console(
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        self.prompt = PromptBuilder(
            self.session,
            self.state,
            self.spinner,
            self.runtime_context.pt_session,
        )
        # Lazy: TurnRunner pulls the agent/action stack — must not load at
        # ``import surfaces.interactive_shell.main``.
        from infrastructure.turn_host.turn_runner import TurnRunner
        from surfaces.interactive_shell.runtime.shell_agent import shell_agent_build_config

        self.turn_runtime = AgentTurnResources(
            session=self.session,
            state=self.state,
            spinner=self.spinner,
            invalidate_prompt=lambda: self.prompt.invalidate_prompt(),
            request_exit=self.prompt.request_exit,
            console=self.service_console,
            turn_handler=TurnRunner(
                console=self.service_console,
                agent_build=shell_agent_build_config(request_exit=self.prompt.request_exit),
                # One handler for the REPL lifetime; /new and /resume rotate
                # session_id on the live handle — keep only that id's agent.
                retain_only_current_session=True,
            ),
        )
        # Prompt echoes belong in the same stream as everything else this turn
        # writes, so an embedding caller captures the whole conversation.
        self.echo_console = console or Console(
            highlight=False, force_terminal=True, color_system="truecolor"
        )
        self.input_reader = PromptInputReader(
            self.prompt,
            self.state,
            self.session,
            self.echo_console,
        )
        self.background: BackgroundTaskPool | None = None
        self.tasks: list[tuple[str, asyncio.Task[None]]] = []

    async def start_interactive_shell(self) -> None:
        with _alert_listener(self.config, self.service_console, existing=self.inbox) as inbox:
            self.inbox = inbox
            self.runtime_context.inbox = inbox
            self._start_runtime_services()
            try:
                if self.prompt.pt_app is None:
                    raise RuntimeError("prompt application was not initialized")
                with patch_prompt_stdout(self.prompt.pt_app, raw=True):
                    # Main input loop: reads prompts and enqueues submitted turns
                    # onto state.queue. The agent turns themselves run in
                    # run_agent_turn_queue, started above in _start_runtime_services.
                    await run_input_loop(
                        state=self.state,
                        session=self.session,
                        background=self.background,
                        input_reader=self.input_reader,
                        echo_console=self.echo_console,
                        handle_input_action=self._handle_input_action,
                    )
            finally:
                await self._shutdown_runtime()

    def _start_runtime_services(self) -> None:
        self.prompt.setup()
        self.background = BackgroundTaskPool(
            self.session,
            self.state,
            self.spinner,
            self.inbox,
            self.prompt.invalidate_prompt,
        )
        self.tasks = self.background.start_all(
            lambda: run_agent_turn_queue(
                state=self.state,
                run_turn=lambda text: run_agent_turn(self.turn_runtime, text),
            )
        )
        # Fleet sampler is lazy: /fleet triggers it on first live use.
        self.session.terminal.fleet_sampler_starter = self.background.ensure_fleet_sampler_started
        try:
            start_loop_scheduler()
        except Exception as exc:  # noqa: BLE001
            log.warning("Loop scheduler could not start: %s", exc)

    async def _handle_input_action(self, action: InputAction) -> bool:
        match action:
            case IgnoreInput():
                return True
            case CloseShell():
                return False
            case CancelTurn(submitted_text=text):
                if text:
                    self.prompt.render_submitted_prompt(self.echo_console, text)
                self.state.cancel_current_dispatch()
                return True
            case DeliverConfirmation(text=text):
                self.state.deliver_confirmation(text)
                return True
            case SubmitTurn(text=text, wait_until_idle=wait, warning=warning):
                # Read before render_submitted_prompt — that clears the autosubmit
                # and handoff-answer flags.
                autosubmitted = bool(self.session.terminal.last_input_autosubmitted)
                ask_user_answers = bool(self.session.terminal.awaiting_handoff_answer)
                if not autosubmitted and not ask_user_answers:
                    # A genuine typed turn starts a new workload: reset the ask-user
                    # round counter so the two-round cap is per-request, not per-session.
                    self.session.ask_user_rounds = 0
                    self.session.terminal.pending_choice_response = None
                    # Clear a finished plan left pinned after the previous turn so
                    # it does not linger over this one. An unfinished plan stays:
                    # the operator may continue it or type ``go`` while idle.
                    # A still-running dispatch keeps its plan even when complete.
                    plan = self.session.task_plan
                    if (
                        plan is not None
                        and plan.all_completed
                        and not self.state.is_dispatch_running()
                    ):
                        self.session.task_plan = None
                # Only exclusive-stdin commands hold the next prompt. A ``/goal``
                # work turn keeps it open: the prompt row is where the spinner,
                # the live tool name and the Auto line are painted, so
                # suspending it left the goal running with no progress signal.
                wait_for_turn = wait
                if wait_for_turn:
                    await self.prompt.suspend()
                if warning:
                    self.echo_console.print(warning)
                self.prompt.render_submitted_prompt(self.echo_console, text)
                await self.state.queue.put(text)
                if wait_for_turn:
                    await self.state.queue.join()
                return True
        raise AssertionError(f"Unhandled input action: {action!r}")

    async def _shutdown_runtime(self) -> None:
        self.state.request_exit()
        self.state.cancel_current_dispatch()
        await self.prompt.close()

        for _label, task in self.tasks:
            task.cancel()

        shutdown_results = await asyncio.gather(
            *(task for _label, task in self.tasks),
            return_exceptions=True,
        )
        for (label, _task), result in zip(self.tasks, shutdown_results, strict=True):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                log.debug("%s task shutdown raised exception: %s", label, result)
        shutdown_loop_scheduler()


__all__ = ["InteractiveShellController"]
