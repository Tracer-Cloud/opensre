"""``AgentSession`` — the embedder's entry point for chat.

Create the session, attach an agent, run turns::

    session = AgentSession.start(config)  # builds the default agent
    result = session.chat("…")            # turn 1
    follow = session.chat("…")            # turn 2 — same attached agent

Embedded scripts that need local adapters use
``bootstrap.embedded.start_embedded_session``. Scheduled one-shots may use
:meth:`AgentSession.run_headless_turn`; a loop keeps one agent for the loop.

A host with its own ports (the gateway pool, the REPL) builds the agent through
:class:`~core.agent_harness.turns.headless_build.DefaultHeadlessBuild` and drives it
with :meth:`HeadlessAgent.handle` per message; it may still attach it here to
use :meth:`chat`.

Session lifecycle (create / resolve / rotate / restore) belongs to
:class:`~core.agent_harness.session.lifecycle.SessionManager`; this module adds
env resolution and prompt context on top. Must not import
``surfaces.interactive_shell``; surfaces inject prompt context through
:class:`SessionConfig`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from core.agent_harness.session import SessionManager

if TYPE_CHECKING:
    from core.agent_harness.ports import (
        OutputSink,
        PromptContextProvider,
        ToolEventObserver,
        ToolProvider,
        TurnBinding,
    )
    from core.agent_harness.session.session_core import SessionCore
    from core.agent_harness.session_goal.goal import SessionGoal
    from core.agent_harness.session_goal.run_until import SessionGoalRunResult
    from core.agent_harness.turns.turn_results import TurnResult
    from core.tool.execution import ToolExecutionHooks


class ChatDispatcher(Protocol):
    """Anything that can run one chat turn for a message (headless or TTY)."""

    def dispatch(self, message: str) -> TurnResult:
        """Run one turn for ``message`` and return its :class:`TurnResult`."""


@runtime_checkable
class GoalDispatcher(ChatDispatcher, Protocol):
    """A dispatcher that also drives the session-goal loop (a :class:`HeadlessAgent`)."""

    def run_goal(
        self,
        text: str,
        binding: TurnBinding | None = None,
        *,
        goal: SessionGoal | None = None,
        evaluate: Callable[..., str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[SessionGoal], None] | None = None,
    ) -> SessionGoalRunResult:
        """Run the session-goal loop for ``text`` and return the full run result."""


@dataclass(frozen=True)
class SessionConfig:
    """What a surface hands :class:`AgentSession` to start up.

    Every field is optional so a surface only opts into the behavior it
    needs: a fresh gateway turn has nothing to resume (``session_id=None``);
    a headless action-only turn has no grounded context (``prompts=None``).
    """

    session_id: str | None = None
    prompts: PromptContextProvider | None = None
    load_env: bool = True
    hydrate_integrations: bool = True
    # None defers to SessionManager's own per-operation default: eager warm on
    # resolve() (a resumed session needs tools ready immediately), lazy on
    # create() (a fresh session can warm on first turn).
    warm_integrations: bool | None = None
    persistent_tasks: bool = True
    open_store: bool = True
    session_manager: SessionManager | None = None
    # Optional process boot run once at :meth:`AgentSession.startup`. A
    # callable, not a flag: ``core`` may not import ``bootstrap`` (package
    # layers), so the host supplies the step. Embedded scripts use
    # :func:`bootstrap.embedded.start_embedded_session`, which fills this
    # with ``configure_process(EMBEDDED_PROFILE)``. CLI, gateway and web
    # already boot their own profile and leave this unset.
    boot_process: Callable[[], None] | None = None


# Scheduled/one-shot runs: a fresh warm session that leaves no persisted task
# registry or open storage behind.
SCHEDULED_RUN_CONFIG = SessionConfig(
    load_env=True,
    hydrate_integrations=True,
    warm_integrations=True,
    persistent_tasks=False,
    open_store=False,
)


@dataclass(frozen=True)
class SessionStartupResult:
    """Outcome of :meth:`AgentSession.startup`."""

    session: SessionCore
    prompts: PromptContextProvider | None


class AgentSession:
    """Public host API: ``start`` / ``chat``.

    Order of startup steps matters: env vars must be resolved before session
    creation (integration hydration/warm may depend on env-provided credentials),
    and context loading is independent of both so it runs last for readability.
    """

    def __init__(self, config: SessionConfig | None = None) -> None:
        self._config = config or SessionConfig()
        self._session_manager = self._config.session_manager or SessionManager()
        self._agent: ChatDispatcher | None = None
        self._bound_session: SessionCore | None = None

    @classmethod
    def start(
        cls,
        config: SessionConfig | None = None,
        *,
        output: OutputSink | None = None,
        prompts: PromptContextProvider | None = None,
        prepare_session: Callable[[SessionCore], None] | None = None,
        tools: ToolProvider | None = None,
        console: Any | None = None,
        logger: logging.Logger | None = None,
        surface: str | None = None,
        is_tty: bool | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
        tool_event_observer: ToolEventObserver | None = None,
        unattended: bool = False,
    ) -> AgentSession:
        """Return a session that is ready to :meth:`chat`.

        Create the session, run ``prepare_session``, resolve the sink and prompt
        context, and attach the default agent. Build it once and dispatch as
        many turns as the caller needs::

            session = AgentSession.start(config)
            for prompt in prompts:
                result = session.chat(prompt)

        Does **not** import ``bootstrap`` — package layers forbid it. Embedded
        hosts that need local adapters use
        :func:`bootstrap.embedded.start_embedded_session` (or pass
        ``SessionConfig(boot_process=…)`` after calling
        ``configure_process``). Surfaces that already booted another profile
        leave ``boot_process`` unset.

        ``prepare_session`` runs after session create (e.g. pin a project scope)
        and before the agent is built. ``console``, ``logger`` and ``surface``
        are the :class:`~core.agent_harness.turns.headless_build.DefaultHeadlessBuild`
        fields; ``tools`` the port its ``agent()`` takes;
        ``is_tty`` and ``tool_hooks`` (the turn's approval hooks) are bound on
        the first turn. ``tool_event_observer`` receives action-tool lifecycle
        events from the default tool provider. A host that needs more (its own
        sink, prompts, error reporter, an action ``llm_factory``) builds through
        :class:`DefaultHeadlessBuild` itself and calls :meth:`attach_agent`.
        """
        from core.agent_harness.turns.headless_adapters import BufferOutputSink

        agent_session = cls(config)
        startup = agent_session.startup()
        if prepare_session is not None:
            prepare_session(startup.session)
        agent_session._bound_session = startup.session
        agent_session._attach_default_headless(
            session=startup.session,
            output=output if output is not None else BufferOutputSink(),
            prompts=prompts if prompts is not None else startup.prompts,
            tools=tools,
            console=console,
            logger=logger,
            surface=surface,
            is_tty=is_tty,
            tool_hooks=tool_hooks,
            tool_event_observer=tool_event_observer,
            unattended=unattended,
        )
        return agent_session

    @classmethod
    def run_headless_turn(
        cls,
        message: str,
        *,
        config: SessionConfig | None = None,
        output: OutputSink | None = None,
        prepare_session: Callable[[SessionCore], None] | None = None,
        logger: logging.Logger | None = None,
        is_tty: bool | None = None,
        unattended: bool = False,
    ) -> TurnResult:
        """Run exactly one turn for ``message`` on a throwaway session.

        Convenience for scheduled digests and report runners that fire once. A
        loop that runs several turns must call :meth:`start` once and
        :meth:`chat` per turn instead — this rebuilds the session, re-hydrates
        integrations, and discards every warm cache on each call.
        """
        return cls.start(
            config or SCHEDULED_RUN_CONFIG,
            output=output,
            prepare_session=prepare_session,
            logger=logger,
            is_tty=is_tty,
            unattended=unattended,
        ).chat(message)

    def startup(self) -> SessionStartupResult:
        """Run process boot (optional), env, session bootstrap/resume, and context.

        :attr:`SessionConfig.boot_process`, when supplied, runs first — an
        embedded host passes ``lambda: configure_process(EMBEDDED_PROFILE)`` so
        tools see the same local integrations as the interactive
        shell. CLI, gateway and web already boot their own profile, so they
        leave it unset. ``configure_process`` is idempotent per profile, so a
        host that boots twice is harmless.
        """
        if self._config.boot_process is not None:
            self._config.boot_process()
        self._resolve_env_variables()
        session = self._load_or_create_session()
        prompts = self._load_context()
        return SessionStartupResult(session=session, prompts=prompts)

    def attach_agent(self, agent: ChatDispatcher) -> None:
        """Bind a chat dispatcher for :meth:`chat` reuse."""
        self._agent = agent

    @property
    def bound_session(self) -> SessionCore | None:
        """The session created by :meth:`start`, for host-side lifecycle (e.g. close)."""
        return self._bound_session

    @property
    def agent(self) -> ChatDispatcher | None:
        """The attached chat dispatcher, if one has been bound."""
        return self._agent

    def chat(
        self,
        message: str,
        *,
        agent: ChatDispatcher | None = None,
    ) -> TurnResult:
        """Run one chat turn for ``message`` (the public chat verb).

        Prefer :meth:`attach_agent` once, then call this per message::

            session.attach_agent(headless)
            session.chat(text)
        """
        target = agent if agent is not None else self._agent
        if target is None:
            raise RuntimeError(
                "AgentSession.chat requires an attached chat dispatcher "
                "(call attach_agent first, or pass agent=)."
            )
        return target.dispatch(message)

    def chat_until_goal(
        self,
        message: str,
        *,
        goal: SessionGoal | None = None,
        evaluate: Callable[..., str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[SessionGoal], None] | None = None,
    ) -> SessionGoalRunResult:
        """Run the session-goal loop for ``message`` until the goal completes.

        Delegates to the attached agent's :meth:`HeadlessAgent.run_goal` — the
        same loop the gateway host runs — so there is one goal loop, not two. No
        binding is passed: the agent was configured once at :meth:`start` /
        :meth:`attach_agent`, so the loop reuses its bound turn context (tool
        hooks, tty) rather than replacing it per turn. Requires an agent that
        drives goals (a :class:`GoalDispatcher`); a dispatch-only agent raises.
        Pass ``goal=`` explicitly, or let the first action turn attach one via a
        ``session_goal:`` handoff tag. Does not scan user prose for intent. Caps
        at ``goal.max_outer_turns``. Honors ``cancel_requested`` between turns.
        ``on_progress`` receives the goal after each turn (checklist UI).
        """
        if self._bound_session is None:
            raise RuntimeError(
                "AgentSession.chat_until_goal requires a bound session "
                "(call AgentSession.start() first)."
            )
        agent = self._agent
        if not isinstance(agent, GoalDispatcher):
            raise RuntimeError(
                "AgentSession.chat_until_goal requires an agent that drives the "
                "session-goal loop (attach a HeadlessAgent via start/attach_agent)."
            )
        return agent.run_goal(
            message,
            goal=goal,
            evaluate=evaluate,
            cancel_requested=cancel_requested,
            on_progress=on_progress,
        )

    def resolve_integrations(self, session: SessionCore) -> dict[str, Any]:
        """Return resolved integration configs for ``session``."""
        from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations

        return resolve_and_cache_integrations(session)

    def _attach_default_headless(
        self,
        *,
        session: SessionCore,
        output: OutputSink,
        prompts: PromptContextProvider | None,
        tools: ToolProvider | None = None,
        console: Any | None = None,
        logger: logging.Logger | None = None,
        surface: str | None = None,
        is_tty: bool | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
        tool_event_observer: ToolEventObserver | None = None,
        unattended: bool = False,
    ) -> None:
        """Attach the agent built on the default port family (one construction recipe).

        Used by :meth:`start` and :meth:`run_headless_turn`. Custom hosts
        (gateway pool, REPL) build through :class:`DefaultHeadlessBuild` themselves and
        call :meth:`attach_agent` — they must not re-copy this wiring ad hoc.
        """
        from core.agent_harness.ports import TurnBinding
        from core.agent_harness.turns.headless_build import DefaultHeadlessBuild

        agent = DefaultHeadlessBuild(
            session=session,
            output=output,
            console=console,
            logger=logger,
            surface=surface,
            tool_event_observer=tool_event_observer,
            unattended=unattended,
        ).agent(tools=tools, prompts=prompts)
        agent.bind_turn(TurnBinding(is_tty=is_tty, tool_hooks=tool_hooks))
        self.attach_agent(agent)

    def _resolve_env_variables(self) -> None:
        """Load local OpenSRE env defaults into the process, once.

        Delegates to :func:`config.local_env.bootstrap_opensre_env_once` so CLI,
        gateway, and web share one path (project ``.env`` + wizard defaults).
        ``override=False``: a variable already set in the real environment wins.
        """
        if self._config.load_env:
            from config.local_env import bootstrap_opensre_env_once

            bootstrap_opensre_env_once(override=False)

    def _load_or_create_session(self) -> SessionCore:
        """Resume a persisted session if ``session_id`` was given, else create one.

        Delegates entirely to :class:`SessionManager` — this method does not
        duplicate its bootstrap/restore logic, it just picks which lifecycle
        call to make based on whether the surface is resuming.
        """
        manager = self._session_manager
        if self._config.session_id:
            # SessionManager.resolve()'s own default is True: a resumed
            # session needs tools ready immediately.
            warm = (
                True if self._config.warm_integrations is None else self._config.warm_integrations
            )
            return manager.resolve(
                self._config.session_id,
                hydrate_integrations=self._config.hydrate_integrations,
                warm_integrations=warm,
                persistent_tasks=self._config.persistent_tasks,
            )
        # SessionManager.create()'s own default is False: a fresh session can
        # warm lazily on first turn.
        warm = False if self._config.warm_integrations is None else self._config.warm_integrations
        return manager.create(
            hydrate_integrations=self._config.hydrate_integrations,
            warm_integrations=warm,
            persistent_tasks=self._config.persistent_tasks,
            open_store=self._config.open_store,
        )

    def _load_context(self) -> PromptContextProvider | None:
        """Return the surface's grounding-context provider, if any."""
        return self._config.prompts


__all__ = [
    "SCHEDULED_RUN_CONFIG",
    "AgentSession",
    "ChatDispatcher",
    "GoalDispatcher",
    "SessionConfig",
    "SessionStartupResult",
]
