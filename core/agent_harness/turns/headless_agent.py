"""The agent a host runs turns on.

Built by a port family (:mod:`core.agent_harness.turns.headless_build`), then
driven one message at a time::

    from core.agent_harness.runtime import InMemoryHeadlessBuild, TurnBinding
    from core.agent_harness.turns.headless_adapters import NullToolProvider

    agent = InMemoryHeadlessBuild().agent(tools=NullToolProvider())
    result = agent.handle("hi there", TurnBinding())
    print(result.primary_response_text)

A host with real ports uses ``DefaultHeadlessBuild`` the same way; the gateway and the
interactive shell are both that program.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import (
    ConfirmFn,
    ConsoleBindable,
    ErrorReporter,
    ExecuteActions,
    LlmFactory,
    OutputBindable,
    OutputSink,
    PromptContextProvider,
    SessionBindable,
    SessionState,
    ToolProvider,
    TurnAccounting,
    TurnBinding,
)
from core.agent_harness.session_goal.evaluate import build_session_goal_evaluator
from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.session_goal.run_until import SessionGoalRunResult, run_until_session_goal
from core.agent_harness.turns.action_driver import ActionTurnRunner
from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
from core.agent_harness.turns.headless_adapters import NoopTurnAccounting
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.tool.execution import ToolExecutionHooks


class AgentBusyError(RuntimeError):
    """A second turn was started on an agent that is still handling one.

    One agent serves one turn at a time: ``bind_turn`` mutates per-turn state,
    so an overlapping turn would read another conversation's binding. Hosts
    that pool agents hold one per logical session and serialize its turns.
    """


class HeadlessAgent:
    """Runs agent turns from a fixed set of ports; built by a port family.

    Every port is required here — the two families supply them:
    :class:`~core.agent_harness.turns.headless_build.InMemoryHeadlessBuild` (in-memory;
    scripts and tests) and :class:`~core.agent_harness.turns.headless_build.DefaultHeadlessBuild`
    (the product defaults; gateway and shell). Hosts do not call this
    constructor.

    Per message a host calls :meth:`handle` with a :class:`TurnBinding`. The
    session-goal loop lives in :meth:`run_goal` — the one driver both
    :meth:`handle` (which returns its last result) and
    :meth:`AgentSession.chat_until_goal` delegate to, so there is one loop, not
    two. :meth:`dispatch` is the single-turn verb underneath; whole-stage
    replacements for tests go through :meth:`bind_stages`. One agent serves
    one turn at a time — an overlapping :meth:`handle`, :meth:`run_goal` or
    :meth:`dispatch` raises :class:`AgentBusyError`.
    """

    def __init__(
        self,
        *,
        tools: ToolProvider,
        session: SessionState,
        output: OutputSink,
        prompts: PromptContextProvider,
        error_reporter: ErrorReporter,
        llm_factory: LlmFactory,
        judge_llm_factory: LlmFactory | None = None,
    ) -> None:
        self._tools = tools
        self._llm_factory = llm_factory
        # Session-goal completion is judged by a cheap model the host injects.
        # No judge: only a fully ticked checklist can close a goal.
        self._goal_evaluate = (
            build_session_goal_evaluator(judge_llm_factory)
            if judge_llm_factory is not None
            else None
        )
        self._session: SessionState = session
        self._output: OutputSink = output
        self._prompts: PromptContextProvider = prompts
        self._error_reporter = error_reporter
        # Turn-scoped state; see bind_turn / bind_stages.
        self._accounting: TurnAccounting | None = None
        self._confirm_fn: ConfirmFn | None = None
        self._is_tty: bool | None = None
        self._tool_hooks: ToolExecutionHooks | None = None
        self._execute_actions_override: ExecuteActions | None = None
        # Reentrant so handle() may call dispatch() on the same thread; a second
        # thread fails the non-blocking acquire and gets AgentBusyError.
        self._turn_lock = threading.RLock()
        self._action_runner = self._new_action_runner()

    def handle(
        self,
        text: str,
        binding: TurnBinding,
        *,
        accounting_factory: Callable[[str], TurnAccounting] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[SessionGoal], None] | None = None,
    ) -> TurnResult:
        """Handle one inbound message and return the goal loop's last turn result.

        The one host loop — gateway and shell call this and nothing else per
        message. A thin wrapper over :meth:`run_goal` for callers that only need
        the final :class:`TurnResult`; :meth:`run_goal` returns the full run.
        """
        return self.run_goal(
            text,
            binding,
            accounting_factory=accounting_factory,
            cancel_requested=cancel_requested,
            on_progress=on_progress,
        ).last_result

    def run_goal(
        self,
        text: str,
        binding: TurnBinding | None = None,
        *,
        goal: SessionGoal | None = None,
        evaluate: Callable[..., str] | None = None,
        accounting_factory: Callable[[str], TurnAccounting] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[SessionGoal], None] | None = None,
    ) -> SessionGoalRunResult:
        """Dispatch, and continue while a session goal is attached; the one loop driver.

        Both the host loop (:meth:`handle`) and
        :meth:`AgentSession.chat_until_goal` run through this, so there is one
        loop, not two. A ``binding`` states the whole turn and is re-applied per
        outer turn — a per-turn host (gateway/shell) passes one so no field is
        inherited across turns (see :class:`TurnBinding`); ``accounting_factory``
        (message → accounting) then varies accounting per outer turn. A host that
        configured the agent once and reuses it (``AgentSession``) omits the
        binding: the agent keeps its bound turn context and ``accounting_factory``
        is not used. ``goal`` attaches an explicit host-owned goal; ``evaluate``
        overrides goal completion, else the injected judge decides.
        ``cancel_requested`` is checked between outer turns; ``on_progress``
        receives the goal after each.
        """

        def _one_turn(message: str) -> TurnResult:
            if binding is not None:
                accounting = accounting_factory(message) if accounting_factory is not None else None
                self.bind_turn(replace(binding, accounting=accounting))
            return self.dispatch(message)

        # The goal loop reads and writes goal state on the session this turn
        # states; with no binding it is the agent's currently bound session.
        session = self._session
        if binding is not None and binding.session is not None:
            session = binding.session
        with self._one_turn_at_a_time():
            return run_until_session_goal(
                _one_turn,
                session,
                text,
                goal=goal,
                evaluate=evaluate if evaluate is not None else self._goal_evaluate,
                cancel_requested=cancel_requested,
                on_progress=on_progress,
            )

    def dispatch(self, message: str) -> TurnResult:
        """Run one turn for ``message`` on the currently bound turn (see :meth:`handle`)."""
        with self._one_turn_at_a_time():
            return dispatch_chat_turn(
                message,
                self._session,
                ChatTurnBindings(
                    execute_actions=self._execute_actions_override or self._execute_actions,
                    accounting=self._take_accounting(message),
                    confirm_fn=self._confirm_fn,
                    is_tty=self._is_tty,
                    surface=self._prompts.surface(),
                    output=self._output,
                ),
            )

    def bind_turn(self, binding: TurnBinding) -> None:
        """Bind the turn's ports and values; the whole binding replaces the previous one.

        Rebinding ``session`` retargets every :class:`SessionBindable` port,
        ``console`` every :class:`ConsoleBindable` port, and a new ``output``
        object every :class:`OutputBindable` port; a new output or a change of
        ``tool_hooks`` also rebuilds the action runner
        — an unchanged one keeps it. Gateway keeps a stable ``BindableOutput``
        and rebinds the transport destination inside it, so it leaves ``output`` unset.
        """
        if binding.session is not None:
            self.bind_session(binding.session)
        if binding.console is not None:
            for port in self._ports():
                if isinstance(port, ConsoleBindable):
                    port.bind_console(binding.console)
        runner_changed = False
        if binding.output is not None and binding.output is not self._output:
            self._output = binding.output
            for port in self._ports():
                if isinstance(port, OutputBindable):
                    port.bind_output(binding.output)
            runner_changed = True
        if binding.tool_hooks is not self._tool_hooks:
            self._tool_hooks = binding.tool_hooks
            runner_changed = True
        self._accounting = binding.accounting
        self._confirm_fn = binding.confirm_fn
        self._is_tty = binding.is_tty
        if runner_changed:
            self._action_runner = self._new_action_runner()

    def bind_session(self, session: SessionState) -> None:
        """Retarget this agent at a freshly resolved session.

        Gateway ``SessionManager.resolve`` returns a new ``SessionCore`` each
        turn (same id, restored state). Cached agents must follow that object
        so tools/prompts see current integrations and chat metadata.

        Every port that implements :class:`~core.agent_harness.ports.SessionBindable`
        is rebound; a session-aware port that lacks the protocol is a type/test
        gap, not a runtime miss.
        """
        self._session = session
        for port in self._ports():
            if isinstance(port, SessionBindable):
                port.bind_session(session)

    def bind_stages(
        self,
        *,
        execute_actions: ExecuteActions | None = None,
    ) -> None:
        """Replace whole stages for the next dispatches; ``None`` restores the port-driven default.

        The per-turn counterpart of the constructor's stage overrides, for a
        long-lived agent that hosts many turns with different injected seams.
        """
        self._execute_actions_override = execute_actions

    @contextmanager
    def _one_turn_at_a_time(self) -> Iterator[None]:
        if not self._turn_lock.acquire(blocking=False):
            raise AgentBusyError("this agent is already handling a turn")
        try:
            yield
        finally:
            self._turn_lock.release()

    def _take_accounting(self, message: str) -> TurnAccounting:
        """Return turn accounting and clear the slot (consume-once).

        A prior turn's ``DefaultTurnAccounting`` (which captures that turn's
        prompt text) must not leak into the next ``dispatch`` when a host
        binds a turn without accounting.
        """
        accounting = self._accounting
        self._accounting = None
        if accounting is not None:
            return accounting
        if hasattr(self._session, "store"):
            return DefaultTurnAccounting(self._session, message)
        return NoopTurnAccounting()

    def _execute_actions(
        self,
        text: str,
        *,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = None,
        turn_plan: TurnPlan | None = None,
    ) -> ToolCallingTurnResult:
        return self._action_runner.run(
            text,
            self._session,
            turn_plan=turn_plan,
            is_tty=is_tty,
            confirm_fn=confirm_fn,
        )

    def _ports(self) -> tuple[object, ...]:
        """Every port this agent holds; rebinding asks each for the capability it needs."""
        return (
            self._tools,
            self._prompts,
            self._error_reporter,
        )

    def _new_action_runner(self) -> ActionTurnRunner:
        return ActionTurnRunner(
            output=self._output,
            tools=self._tools,
            llm_factory=self._llm_factory,
            error_reporter=self._error_reporter,
            tool_hooks=self._tool_hooks,
        )


__all__ = ["AgentBusyError", "HeadlessAgent"]
