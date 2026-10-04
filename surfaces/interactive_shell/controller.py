"""Top-level interactive-shell controller: prompt input, dispatch, and shutdown."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from prompt_toolkit import PromptSession
from rich.console import Console

from config.repl_config import ReplConfig
from core.agent_harness.spi.cancel import HostCancelReason
from core.agent_harness.spi.session_goal import (
    apply_session_goal_control,
    session_goal_is_active,
)
from core.agent_harness.spi.session_state import clear_setup_resume
from core.agent_harness.spi.task_plan import discard_task_plan
from core.domain.alerts import inbox as _alert_inbox
from infrastructure.turn_host.session_lock import (
    SessionExecutionBusyError,
    session_execution_lock,
)
from surfaces.interactive_shell.runtime.background.workers import BackgroundTaskPool
from surfaces.interactive_shell.runtime.ci_fix_status import bind_ci_fix_status
from surfaces.interactive_shell.runtime.context import (
    ReplRuntime,
    create_repl_runtime,
)
from surfaces.interactive_shell.runtime.core.prompt_builder import PromptBuilder
from surfaces.interactive_shell.runtime.core.state import (
    ReplState,
    SpinnerState,
)
from surfaces.interactive_shell.runtime.exit_control import (
    finish_shell_exit,
    record_inflight_shell_exit,
)
from surfaces.interactive_shell.runtime.goal_controls import (
    mark_inflight_goal_control,
)
from surfaces.interactive_shell.runtime.input import (
    PromptInputReader,
)
from surfaces.interactive_shell.runtime.input.actions import (
    CloseShell,
    DeliverConfirmation,
    IgnoreInput,
    InflightControl,
    InputAction,
    RunInflightControl,
    SubmitTurn,
    goal_control_reason,
)
from surfaces.interactive_shell.runtime.loop_scheduler import (
    shutdown_loop_scheduler,
    start_loop_scheduler,
)
from surfaces.interactive_shell.runtime.session_shutdown import (
    close_repl_session_after_detached_worker,
)
from surfaces.interactive_shell.runtime.startup.deferred_work import DeferredStartupWork
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

_GOAL_CONTROL_LOCK_RETRY_SECONDS = 0.1
_INFLIGHT_EXIT_DRAIN_TIMEOUT_SECONDS = 5.0


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
        startup_work: DeferredStartupWork | None = None,
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
        if startup_work is None:
            # No launch to protect (embedded shell): background work starts at once.
            startup_work = DeferredStartupWork()
            startup_work.release()
        self.startup_work = startup_work
        self.background: BackgroundTaskPool | None = None
        self.tasks: list[tuple[str, asyncio.Task[None]]] = []
        self._ci_fix_status_cleanup: Callable[[], None] | None = None
        self._finish_exit_on_shutdown = False
        self._inflight_exit_command: str | None = None
        self._deferred_session_close_registered = False
        self._unsaved_detached_goal_control: HostCancelReason | None = None

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
                        has_live_turn_worker=self.turn_runtime.has_live_turn_worker,
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
            defer_thread_job=self.startup_work.defer,
        )
        self.tasks = self.background.start_all(
            lambda: run_agent_turn_queue(
                state=self.state,
                run_turn=self._run_turn,
                on_goal_control=self._apply_goal_control_at_turn_boundary,
            )
        )
        # Fleet sampler is lazy: /fleet triggers it on first live use.
        self.session.terminal.fleet_sampler_starter = self.background.ensure_fleet_sampler_started
        try:
            start_loop_scheduler(host_session=lambda: self.session.session_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Loop scheduler could not start: %s", exc)
        self._ci_fix_status_cleanup = bind_ci_fix_status(self.session.terminal)
        if self.session.pending_user_choice is None:
            # No startup menu will draw: the prompt is the first thing the user
            # waits on. Released only now, so every job above is already held.
            self.startup_work.release()

    async def _run_turn(self, text: str) -> None:
        try:
            await run_agent_turn(self.turn_runtime, text)
        finally:
            # Normally the startup menu's draw released held work already; a
            # first turn that ended without drawing one must not keep it held.
            self.startup_work.release()

    def _try_apply_goal_control_after_worker_release(
        self,
        reason: HostCancelReason,
    ) -> bool:
        """Apply and persist a goal control after the worker releases ownership."""
        try:
            with session_execution_lock(self.session.session_id, timeout=0):
                if apply_session_goal_control(self.session, reason):
                    self.session.store.flush_session_goal_control_state(self.session)
        except SessionExecutionBusyError:
            return False
        except OSError:
            log.warning("Could not persist goal control at the turn boundary", exc_info=True)
        return True

    async def _apply_goal_control_at_turn_boundary(
        self,
        reason: HostCancelReason,
    ) -> None:
        """Serialize a durable goal control without blocking the event loop."""
        while not await asyncio.to_thread(
            self._try_apply_goal_control_after_worker_release,
            reason,
        ):
            await asyncio.sleep(_GOAL_CONTROL_LOCK_RETRY_SECONDS)

    def _persist_goal_control_for_resume(self, reason: HostCancelReason) -> str:
        """Preserve goal intent without mutating state owned by a detached worker."""
        from core.agent_harness import SessionManager

        return SessionManager.for_session(self.session).persist_session_goal_control(
            self.session,
            reason.value,
        )

    def _finalize_detached_goal_control(
        self,
        reason: HostCancelReason,
        control_id: str | None,
    ) -> None:
        """Apply a control once a detached worker releases the session lease."""
        try:
            with session_execution_lock(self.session.session_id):
                apply_session_goal_control(self.session, reason)
                self.session.store.flush_session_goal_control_state(self.session)
                if control_id is not None:
                    self.session.store.complete_session_goal_control(
                        self.session.session_id,
                        control_id,
                    )
        except Exception:
            # The requested sidecar record remains unacknowledged and restore
            # will retry it.  Do not race the worker by applying it early.
            log.warning("Could not finalize detached goal control", exc_info=True)
        else:
            if self._unsaved_detached_goal_control is reason:
                self._unsaved_detached_goal_control = None

    async def _handle_input_action(self, action: InputAction) -> bool:
        match action:
            case IgnoreInput():
                return True
            case CloseShell():
                return False
            case RunInflightControl(
                control=InflightControl.CANCEL_TURN,
                submitted_text=text,
            ):
                if text:
                    self.prompt.render_submitted_prompt(self.echo_console, text)
                self.state.cancel_current_dispatch()
                return True
            case RunInflightControl(
                control=InflightControl.EXIT_SHELL,
                submitted_text=text,
            ):
                self.prompt.render_submitted_prompt(self.echo_console, text)
                self.state.request_exit()
                self.state.signal_current_dispatch()
                self._finish_exit_on_shutdown = True
                self._inflight_exit_command = text
                return False
            case RunInflightControl(control=control, submitted_text=text) if (
                reason := goal_control_reason(control)
            ) is not None:
                if (
                    self.turn_runtime.has_live_turn_worker()
                    and not self.state.is_dispatch_running()
                ):
                    # Esc can cancel the asyncio waiter while the daemon turn
                    # still owns session state.  A queued slash command could
                    # then run concurrently (or be discarded by /exit), so
                    # record the intent now and replay it after the worker
                    # releases its lease instead.
                    self.prompt.render_submitted_prompt(self.echo_console, text)
                    try:
                        control_id = self._persist_goal_control_for_resume(reason)
                    except Exception:
                        # Keep the shell alive and still attempt the safe-boundary
                        # mutation after the worker exits.  The finalizer will
                        # retry its state flush; an unavailable store must not
                        # turn a typed goal control into an unexpected shell exit.
                        log.warning("Could not save detached goal control", exc_info=True)
                        control_id = None
                        self._unsaved_detached_goal_control = reason
                    self.turn_runtime.run_after_turn_worker(
                        functools.partial(
                            self._finalize_detached_goal_control,
                            reason,
                            control_id,
                        )
                    )
                    return True
                # Keep slash execution serialized through the normal turn
                # queue, but signal current work now. The queue owner applies
                # the state transition after the worker thread returns, before
                # any already-queued input, so goal tools remain single-owner.
                self.prompt.render_submitted_prompt(self.echo_console, text)
                self.state.request_goal_control(
                    reason,
                    interrupt=session_goal_is_active(self.session),
                )
                if self.state.requested_goal_control() is not None:
                    mark_inflight_goal_control(self.session, reason)
                await self.state.queue.put(text)
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
                    # The user moved on, so a turn parked behind setup is not replayed.
                    clear_setup_resume(self.session)
                    # Clear a finished plan left pinned after the previous turn so
                    # it does not linger over this one. An unfinished plan stays:
                    # the operator may continue it or type ``go`` while idle.
                    # A still-running dispatch keeps its plan even when complete.
                    plan = self.session.task_plan
                    if (
                        plan is not None
                        and plan.is_settled
                        and not self.state.is_dispatch_running()
                    ):
                        discard_task_plan(self.session)
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
        if self._ci_fix_status_cleanup is not None:
            self._ci_fix_status_cleanup()
            self._ci_fix_status_cleanup = None
        active_turn = self.state.current_task
        detached_goal_control = self._unsaved_detached_goal_control
        detached_goal_control_saved = False
        detached_exit_command: str | None = None
        self.state.request_exit()
        if active_turn is not None and not active_turn.done():
            self.state.signal_current_dispatch()
        else:
            self.state.cancel_current_dispatch()
        await self.prompt.close()

        if active_turn is not None and not active_turn.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(active_turn),
                    timeout=_INFLIGHT_EXIT_DRAIN_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                log.warning("In-flight exit turn did not drain before shutdown")
                self.state.mark_turn_worker_detached()
                detached_goal_control = self.state.requested_goal_control() or detached_goal_control
                if detached_goal_control is not None:
                    try:
                        self._persist_goal_control_for_resume(detached_goal_control)
                        detached_goal_control_saved = True
                    except Exception:
                        log.warning(
                            "Could not save pending goal control before forced exit; "
                            "resume may restore the prior goal",
                            exc_info=True,
                        )
                self.state.cancel_current_dispatch()
                await asyncio.gather(active_turn, return_exceptions=True)
            except asyncio.CancelledError:
                log.debug("In-flight exit turn was cancelled before it drained")
            except Exception as exc:
                log.debug("In-flight exit turn ended with exception: %s", exc)

        for _label, task in self.tasks:
            task.cancel()

        shutdown_results = await asyncio.gather(
            *(task for _label, task in self.tasks),
            return_exceptions=True,
        )
        for (label, _task), result in zip(self.tasks, shutdown_results, strict=True):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                log.debug("%s task shutdown raised exception: %s", label, result)
        self.startup_work.close()
        shutdown_loop_scheduler()
        if self.turn_runtime.has_live_turn_worker():
            self.state.mark_turn_worker_detached()
        if detached_goal_control is None:
            detached_goal_control = self._unsaved_detached_goal_control
        if self._finish_exit_on_shutdown:
            self._finish_exit_on_shutdown = False
            if self._inflight_exit_command is not None:
                if self.state.has_detached_turn_worker():
                    detached_exit_command = self._inflight_exit_command
                else:
                    record_inflight_shell_exit(self.session, self._inflight_exit_command)
                self._inflight_exit_command = None
            finish_shell_exit(self.session, self.service_console)
        if self.state.has_detached_turn_worker() and not self._deferred_session_close_registered:
            self._deferred_session_close_registered = True
            self.turn_runtime.run_after_turn_worker(
                functools.partial(
                    close_repl_session_after_detached_worker,
                    self.session,
                    None if detached_goal_control_saved else detached_goal_control,
                    detached_exit_command,
                )
            )


__all__ = ["InteractiveShellController"]
