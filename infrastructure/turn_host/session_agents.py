"""Session-scoped :class:`HeadlessAgent` pool for the turn runner.

Keeps agent construction out of :class:`TurnRunner` so the handler
stays a thin dispatch/finalize orchestrator. Construction goes through
:meth:`~core.agent_harness.turns.headless_build.DefaultHeadlessBuild.agent`
once per session — not a second port-construction path.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console

from core.agent_harness import SessionCore, SessionManager
from core.agent_harness.ports import SlashPortsFactory
from core.agent_harness.runtime import (
    AgentBuildConfig,
    DefaultHeadlessBuild,
    DefaultToolProvider,
    DescribeTool,
    HeadlessAgent,
    resolve_agent_ports,
)
from infrastructure.turn_host.bindable_output import BindableOutput
from infrastructure.turn_host.capability_policy import ensure_gateway_capability_policy
from infrastructure.turn_host.session_lock import session_execution_lock
from infrastructure.turn_host.status_messages import status_from_tool_start
from infrastructure.turn_host.turn_output import TurnOutput


class _ToolStatusObserver:
    """Push live tool-progress status lines to the turn's bound output."""

    def __init__(self, output: BindableOutput, describe: DescribeTool | None) -> None:
        self._output = output
        self._describe = describe

    def __call__(self, kind: str, data: dict[str, object]) -> None:
        if kind != "tool_start":
            return
        tool_name = str(data.get("name") or "").strip()
        if not tool_name:
            return
        self._output.set_tool_status(
            status_from_tool_start(tool_name, data.get("input"), describe=self._describe)
        )


class SessionAgentPool:
    """One :class:`HeadlessAgent` (+ live output) per logical session id."""

    def __init__(
        self,
        *,
        console: Console,
        slash_ports_factory: SlashPortsFactory | None = None,
        agent_build: AgentBuildConfig | None = None,
        retain_only_current_session: bool = False,
    ) -> None:
        self._console = console
        self._slash_ports_factory = slash_ports_factory
        self._build = (
            agent_build
            if agent_build is not None
            else AgentBuildConfig(
                apply_capability_policy=ensure_gateway_capability_policy,
            )
        )
        # Interactive shell keeps one TurnRunner for the REPL lifetime while
        # /new and /resume rotate session_id in place. When this flag is set,
        # handing out an agent for the live id drops every other cached entry
        # so rotations do not accumulate unreachable agents, outputs, and locks.
        self._retain_only_current_session = retain_only_current_session
        self._agents: dict[str, HeadlessAgent] = {}
        self._outputs: dict[str, BindableOutput] = {}
        # One agent serves every turn of a session, and each turn rebinds its
        # session and live output. Turns for the same session must therefore not
        # overlap, or one turn's output goes to the other's output. Different
        # sessions are independent and stay concurrent.
        self._session_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def drop_session(self, session_id: str) -> None:
        """Forget a session's cached agent, bindable output, and lock."""
        if not session_id:
            return
        self._agents.pop(session_id, None)
        self._outputs.pop(session_id, None)
        with self._locks_guard:
            self._session_locks.pop(session_id, None)

    def _drop_sessions_except(self, keep_session_id: str) -> None:
        for session_id in tuple(self._agents):
            if session_id != keep_session_id:
                self.drop_session(session_id)

    def _lock_for(self, session_id: str) -> threading.Lock:
        """The lock guarding one session's agent, created on first use."""
        with self._locks_guard:
            return self._session_locks.setdefault(session_id, threading.Lock())

    @contextmanager
    def session_agent(
        self,
        *,
        session: SessionCore,
        output: TurnOutput,
        logger: logging.Logger,
    ) -> Iterator[HeadlessAgent]:
        """Hold this session's agent for the whole turn.

        The lock spans dispatch, not just the handout: rebinding is what makes
        the agent turn-specific, so releasing before the turn finishes would
        let the next turn retarget an agent that is still streaming.
        """
        session_id = str(getattr(session, "session_id", "") or "")
        if not session_id:
            # No id means no cache entry and nothing shared to protect.
            yield self.agent_for(session=session, output=output, logger=logger)
            return
        # Take the cross-host lease first, matching TurnRunner.run.  A direct
        # pool caller that waited on another host must not hold this process's
        # agent lock while a runner holds the lease and waits for that lock.
        with session_execution_lock(session_id, reentrant=True), self._lock_for(session_id):
            # Gateway ingress resolves before taking this cross-host lease. A
            # CLI resume could have completed while it waited, so reload the
            # persisted branch before this turn binds or later flushes it.
            SessionManager.for_session(session).refresh_from_storage(session)
            yield self.agent_for(session=session, output=output, logger=logger)

    def agent_for(
        self,
        *,
        session: SessionCore,
        output: TurnOutput,
        logger: logging.Logger,
    ) -> HeadlessAgent:
        """Return a session-scoped agent with ``output`` bound for this turn.

        Prefer :meth:`session_agent`, which holds the session's lock for the
        whole turn. This is the unsynchronised primitive it wraps.
        """
        policy = self._build.apply_capability_policy
        if policy is not None:
            policy(session)
        session_id = str(getattr(session, "session_id", "") or "")
        if self._retain_only_current_session and session_id:
            self._drop_sessions_except(session_id)
        session_output = self._outputs.get(session_id) if session_id else None
        if session_output is None:
            session_output = BindableOutput()
            if session_id:
                self._outputs[session_id] = session_output
        session_output.bind(output)

        cached = self._agents.get(session_id) if session_id else None
        if cached is not None:
            # Resolve returns a new SessionCore each turn; keep the cached agent
            # but point every session-scoped port at the current object.
            cached.bind_session(session)
            return cached

        build = self._build
        observer = _ToolStatusObserver(session_output, build.describe_tool)

        def default_tools() -> DefaultToolProvider:
            return DefaultToolProvider(
                session,
                self._console,
                tool_action_logger=logger,
                observer_factory=lambda _message: observer,
                subprocess_presenter_factory=build.subprocess_presenter_factory,
                slash_ports_factory=self._slash_ports_factory,
            )

        tools, prompts = resolve_agent_ports(
            build,
            session=session,
            console=self._console,
            logger=logger,
            observer=observer,
            default_tools=default_tools,
        )
        agent = DefaultHeadlessBuild(
            session=session,
            output=session_output,
            console=self._console,
            logger=logger,
            surface="gateway",
            error_reporter=build.error_reporter,
        ).agent(tools=tools, prompts=prompts)
        if session_id:
            self._agents[session_id] = agent
        return agent

    @property
    def cached_session_ids(self) -> frozenset[str]:
        """Session ids that currently hold a reused agent (test/observability)."""
        return frozenset(self._agents)


__all__ = ["SessionAgentPool"]
