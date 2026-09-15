"""Dispatch one inbound message through the shared headless agent.

This is the **only** turn runner. Transport dispatchers
(Slack/Discord/Telegram) are ingress adapters: they authorize, resolve a
session, build turn output, then call this callback. Process-wide capacity is an
optional gate on the same object — not a second runner.

Transport-agnostic: takes ``(text, session, output, logger)``, runs the turn, and
finalizes outbound text on the output. Agent reuse is handled by
:class:`SessionAgentPool`.

Two entries, one turn. :meth:`TurnRunner.__call__` is the
``TurnCallback`` the four chat transports use and returns nothing.
:meth:`TurnRunner.run` is the same turn for an in-process caller that
needs the outcome as a value and has a terminal to bind — it returns the
``TurnResult`` (``None`` at capacity) and accepts the caller's console,
``confirm_fn`` and ``is_tty``. Every keyword defaults to the transport path, so
the two entries cannot drift apart.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext

from rich.console import Console

from core.agent_harness import SessionCore, SessionManager, TurnResult
from core.agent_harness.ports import ConfirmFn, SlashPortsFactory, TurnAccounting
from core.agent_harness.runtime import AgentBuildConfig, TurnBinding
from core.agent_harness.spi.cancel import ensure_turn_cancel, host_cancel_requested
from core.agent_harness.spi.session_goal import (
    SessionGoal,
    format_session_goal_progress,
    format_session_goal_status_line,
)
from infrastructure.analytics.capture import (
    capture_gateway_turn_completed,
    capture_gateway_turn_failed,
    capture_gateway_turn_started,
)
from infrastructure.analytics.usage_context import (
    CANONICAL_SURFACES,
    bound_usage_context,
    get_surface,
)
from infrastructure.observability.trace.spans import traced_session
from infrastructure.process.turn_capacity import turn_slot
from infrastructure.turn_host.cancel_console import CancelConsole
from infrastructure.turn_host.concurrency import AT_CAPACITY_MESSAGE, TurnConcurrencyGate
from infrastructure.turn_host.session_agents import SessionAgentPool
from infrastructure.turn_host.session_lock import (
    retained_session_execution_locks,
    session_execution_lock,
)
from infrastructure.turn_host.status_messages import EMPTY_RESPONSE_MESSAGE
from infrastructure.turn_host.turn_memory import log_turn_memory, resident_memory_bytes
from infrastructure.turn_host.turn_output import TurnOutput


class TurnRunner:
    """Services one inbound gateway message per call (a :data:`TurnCallback`).

    One :class:`HeadlessAgent` is kept per logical session and reused across
    turns; each message goes through :meth:`HeadlessAgent.handle` with a
    :class:`TurnBinding` (output hooks, cancel console) — the same call the
    interactive shell makes. Concurrent turns for different sessions stay
    isolated.

    When ``gate`` is set, capacity is checked here before the turn runs — the
    manager must not wrap this class in a second "turn runner".
    """

    def __init__(
        self,
        *,
        console: Console,
        slash_ports_factory: SlashPortsFactory | None = None,
        agent_build: AgentBuildConfig | None = None,
        gate: TurnConcurrencyGate | None = None,
        admission_check: Callable[[], bool] | None = None,
        busy_message: str = AT_CAPACITY_MESSAGE,
        retain_only_current_session: bool = False,
    ) -> None:
        self._console = console
        self._pool = SessionAgentPool(
            console=console,
            slash_ports_factory=slash_ports_factory,
            agent_build=agent_build,
            retain_only_current_session=retain_only_current_session,
        )
        # Gateway already bootstrapped env at process start; turns must not reload.
        self._gate = gate
        self._admission_check = admission_check
        self._busy_message = busy_message

    def drop_session(self, session_id: str) -> None:
        """Drop a pooled agent for ``session_id`` (after /new, /resume, or chat rotate)."""
        self._pool.drop_session(session_id)

    def __call__(
        self,
        text: str,
        session: SessionCore,
        output: TurnOutput,
        logger: logging.Logger,
    ) -> None:
        """The :data:`TurnCallback`: chat transports reply through the output."""
        self.run(text, session, output, logger)

    def run(
        self,
        text: str,
        session: SessionCore,
        output: TurnOutput,
        logger: logging.Logger,
        *,
        console: Console | None = None,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = False,
        accounting_factory: Callable[[str], TurnAccounting] | None = None,
        on_progress: Callable[[SessionGoal], None] | None = None,
    ) -> TurnResult | None:
        """Run one admitted turn, or return ``None`` when a gate rejects it.

        Same turn as :meth:`__call__` — one capacity gate, one agent pool, one
        ``handle`` call. The keywords carry a caller's terminal context; every
        default is what a chat transport gets, so omitting them all is the
        transport path exactly — including ``on_progress``, which falls back
        to the compact status line a chat placeholder can hold.

        ``None`` means no agent work ran, for one of three reasons: the capacity
        gate refused the turn (the at-capacity sentence is already finalized on
        the output), the host cancelled it before it started, or the optional
        admission hook rejected it. Only the first finalizes anything here — a
        cancelling host and a rejecting hook each own their user-facing response.
        """
        session_id = str(getattr(session, "session_id", "") or "")
        # A session already being resumed elsewhere must not consume the process
        # turn budget while it waits.  The pool takes this same reentrant lease
        # around agent binding, preserving safety for direct pool callers.
        lease = session_execution_lock(session_id) if session_id else nullcontext()
        # /resume may non-blockingly claim a second session while this turn is
        # running. Keep that target protected until _run_turn has flushed its
        # rebound state, then release it together with this turn's source lease.
        with lease, retained_session_execution_locks(), turn_slot(self._gate) as running:
            if not running:
                output.finalize(self._busy_message)
                return None
            if host_cancel_requested(output):
                return None
            # Admission runs inside the slot on purpose: a hook that meters the
            # turn must not charge for work capacity would have refused. The
            # cost is that a blocking hook holds a slot it has not used yet —
            # on the SMALL profile that is the process's only slot, so a hook
            # doing I/O should keep its timeout well under a turn's duration.
            if self._admission_check is not None and not self._admission_check():
                return None
            # Admission may have consumed a credit. From here the turn lifecycle
            # must run so transports cannot acknowledge a paid delivery without
            # dispatching it.
            return self._run_turn(
                text,
                session,
                output,
                logger,
                console=console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                accounting_factory=accounting_factory,
                on_progress=on_progress,
            )

    def _run_turn(
        self,
        text: str,
        session: SessionCore,
        output: TurnOutput,
        logger: logging.Logger,
        *,
        console: Console | None,
        confirm_fn: ConfirmFn | None,
        is_tty: bool | None,
        accounting_factory: Callable[[str], TurnAccounting] | None,
        on_progress: Callable[[SessionGoal], None] | None,
    ) -> TurnResult:
        session_id = getattr(session, "session_id", None)
        surface = get_surface()
        if surface not in CANONICAL_SURFACES:
            # Require transport binding (Slack/Telegram dispatchers). Do not invent
            # a non-canonical surface that breaks channel breakdowns.
            logger.warning("gateway_turn missing surface binding; started/completed omit surface")
            surface = None
        started = time.monotonic()
        memory_before = resident_memory_bytes()

        cancel = ensure_turn_cancel(output)
        turn_console = CancelConsole(console or self._console, cancel)
        with (
            bound_usage_context(session_id=session_id),
            traced_session(session_id, component="gateway_turn"),
            # Held for the whole turn: the pooled agent's session, output and
            # accounting are rebound per message, so an overlapping turn for the
            # same session would retarget an agent that is still dispatching.
            self._pool.session_agent(session=session, output=output, logger=logger) as agent,
        ):
            try:
                if surface:
                    capture_gateway_turn_started(surface=surface)

                def _cancel_requested() -> bool:
                    return isinstance(cancel, threading.Event) and cancel.is_set()

                def _status_line_progress(goal: SessionGoal) -> None:
                    # A channel with no terminal gets one compact status line and
                    # the full text in debug logs. A caller that can render more
                    # passes its own ``on_progress`` instead of using this.
                    full = format_session_goal_progress(goal, session=session)
                    compact = format_session_goal_status_line(goal, session=session)
                    if full:
                        logger.debug("gateway_session_goal_progress\n%s", full)
                    if compact:
                        set_status = getattr(output, "set_tool_status", None)
                        if callable(set_status):
                            set_status(compact)

                turn_result = agent.handle(
                    text,
                    TurnBinding(
                        session=session,
                        tool_hooks=getattr(output, "tool_hooks", None),
                        console=turn_console,
                        confirm_fn=confirm_fn,
                        is_tty=is_tty,
                    ),
                    accounting_factory=accounting_factory,
                    cancel_requested=_cancel_requested,
                    on_progress=on_progress or _status_line_progress,
                )
                outbound_text = turn_result.primary_response_text
                logger.debug(
                    "gateway_turn done intent=%s answered=%s outbound_chars=%s",
                    turn_result.final_intent,
                    turn_result.answered,
                    len(outbound_text),
                )
                log_turn_memory(logger, memory_before)
                # Host soft-timeout (or stop) already owns the output terminal
                # message — do not overwrite it with empty/fallback finalize.
                cancelled = isinstance(cancel, threading.Event) and cancel.is_set()
                # A streamed agent conclusion already resolved the placeholder.
                # Self-rendering tools still need the captured response finalized.
                if not turn_result.action_result.response_streamed and not cancelled:
                    output.finalize(outbound_text or EMPTY_RESPONSE_MESSAGE)
                # Resolve rebuilds SessionCore from disk next inbound message —
                # persist session_goal (attach / progress / /goal pause) now.
                SessionManager.for_session(session).flush(session)
                if surface:
                    capture_gateway_turn_completed(
                        surface=surface,
                        duration_ms=(time.monotonic() - started) * 1000.0,
                        answered=bool(turn_result.answered),
                        final_intent=str(turn_result.final_intent or "") or None,
                    )
                return turn_result
            except Exception as exc:
                # Always emit failure analytics (surface optional) so misconfigured
                # transports remain visible in PostHog.
                capture_gateway_turn_failed(
                    surface=surface,
                    duration_ms=(time.monotonic() - started) * 1000.0,
                    error_type=type(exc).__name__,
                )
                raise


__all__ = ["TurnRunner"]
