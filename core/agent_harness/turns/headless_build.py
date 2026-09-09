"""The two ways a HeadlessAgent is built: in-memory and product defaults.

**This is the single construction seam.** :class:`InMemoryHeadlessBuild` is
session + output in memory (scripts, tests, a turn with zero configuration).
:class:`DefaultHeadlessBuild` is session + output + console + logger +
error reporter (gateway ``SessionAgentPool``, the REPL, ``AgentSession.start``).
Both build through ``.agent(tools=…, prompts=…)``; hosts never
construct :class:`HeadlessAgent` directly.

Per-message values are bound on the agent with :meth:`HeadlessAgent.handle`
(a :class:`~core.agent_harness.ports.TurnBinding`). Process boot
(``configure_process``) does not build agents.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from io import StringIO
from typing import TYPE_CHECKING, Any

from rich.console import Console

from core.agent_harness.agent_build_config import AgentBuildConfig
from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.llm_resolution import (
    default_classification_llm_factory,
    default_llm_factory,
)
from core.agent_harness.ports import (
    ErrorReporter,
    LlmFactory,
    OutputSink,
    PromptContextProvider,
    SessionState,
    ToolEventObserver,
    ToolProvider,
)
from core.agent_harness.prompts.grounding import (
    DefaultPromptContextProvider,
    supports_default_prompt_context,
)
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    InMemorySessionState,
    NoopErrorReporter,
)
from core.agent_harness.turns.headless_agent import HeadlessAgent
from infrastructure.harness_providers import resolve_subprocess_presenter

if TYPE_CHECKING:
    from core.agent_harness.session.session_core import SessionCore


@dataclass(frozen=True)
class InMemoryHeadlessBuild:
    """The in-memory family: a turn runs with zero configuration.

    ``session`` defaults to an in-memory state and ``output`` to a buffer sink.
    ``tools`` is required on ``agent()``.
    """

    session: SessionState | None = None
    output: OutputSink | None = None

    @cached_property
    def _session(self) -> SessionState:
        return self.session if self.session is not None else InMemorySessionState()

    @cached_property
    def _output(self) -> OutputSink:
        return self.output if self.output is not None else BufferOutputSink()

    def prompts(self) -> PromptContextProvider:
        if supports_default_prompt_context(self._session):
            return DefaultPromptContextProvider(self._session)
        return EmptyPromptContextProvider()

    def agent(
        self,
        *,
        tools: ToolProvider,
        prompts: PromptContextProvider | None = None,
        llm_factory: LlmFactory | None = None,
    ) -> HeadlessAgent:
        return HeadlessAgent(
            tools=tools,
            session=self._session,
            output=self._output,
            prompts=prompts if prompts is not None else self.prompts(),
            error_reporter=NoopErrorReporter(),
            llm_factory=llm_factory if llm_factory is not None else default_llm_factory,
        )


@dataclass(frozen=True)
class DefaultHeadlessBuild:
    """The default adapters for ``session`` streaming to ``output``.

    ``console`` and ``logger`` feed only the defaults (tool rendering, tool
    action log, error reporting) and default to headless-safe instances;
    ``surface`` selects the prompt profile of the default prompt provider;
    ``error_reporter`` replaces the default reporter for every stage.
    """

    session: SessionCore
    output: OutputSink
    console: Any | None = None
    logger: logging.Logger | None = None
    surface: str | None = None
    #: A host's reporter for swallowed exceptions (the REPL adds Sentry); default logs.
    error_reporter: ErrorReporter | None = None
    #: Restrict unattended ticks to NONE / READ_ONLY tools.
    unattended: bool = False

    @cached_property
    def _console(self) -> Any:
        return (
            self.console
            if self.console is not None
            else Console(force_terminal=False, file=StringIO())
        )

    @cached_property
    def _logger(self) -> logging.Logger:
        return self.logger if self.logger is not None else logging.getLogger("opensre")

    @cached_property
    def _error_reporter(self) -> ErrorReporter:
        return (
            self.error_reporter
            if self.error_reporter is not None
            else DefaultErrorReporter(self._logger)
        )

    def tools(self) -> ToolProvider:
        """A bare :class:`DefaultToolProvider`; hosts pass their own configured one to :meth:`agent`.

        The presenter factory is the one registered at process boot so
        ``shell_run`` can execute. A host that wants a different presenter
        passes its own :class:`DefaultToolProvider`.
        """
        return DefaultToolProvider(
            self.session,
            self._console,
            tool_action_logger=self._logger,
            subprocess_presenter_factory=resolve_subprocess_presenter(),
            unattended=self.unattended,
        )

    def prompts(self) -> PromptContextProvider:
        if self.surface is not None:
            return DefaultPromptContextProvider(self.session, surface=self.surface)
        return DefaultPromptContextProvider(self.session)

    def agent(
        self,
        *,
        tools: ToolProvider | None = None,
        prompts: PromptContextProvider | None = None,
        llm_factory: LlmFactory | None = None,
    ) -> HeadlessAgent:
        """The agent on this family; each port a host passes replaces that default.

        ``is not None`` rather than ``or``: a provider defining ``__bool__``
        must not be silently replaced.
        """
        return HeadlessAgent(
            session=self.session,
            output=self.output,
            tools=tools if tools is not None else self.tools(),
            prompts=prompts if prompts is not None else self.prompts(),
            error_reporter=self._error_reporter,
            llm_factory=llm_factory if llm_factory is not None else default_llm_factory,
            judge_llm_factory=default_classification_llm_factory,
        )


def resolve_agent_ports(
    config: AgentBuildConfig,
    *,
    session: Any,
    console: Any,
    logger: logging.Logger,
    observer: ToolEventObserver | None = None,
    default_tools: Callable[[], ToolProvider] | None = None,
) -> tuple[ToolProvider | None, PromptContextProvider | None]:
    """Resolve ``(tools, prompts)`` from a host's :class:`AgentBuildConfig`.

    The single expansion of ``build_tools`` / ``build_prompts``, each falling
    back to ``default_tools`` / ``None`` when the hook is omitted. Callers
    pass the result to ``DefaultHeadlessBuild(...).agent(...)``. The gateway
    pool and the shell builder share this so both expand a config the same way.

    ``config.apply_capability_policy`` is the caller's to run, not this
    function's: the pool applies it every turn (the session is re-resolved even
    on a cached agent), so binding it here would skip it on cache hits.
    """
    if config.build_tools is not None:
        tools: ToolProvider | None = config.build_tools(session, console, logger, observer)
    elif default_tools is not None:
        tools = default_tools()
    else:
        tools = None
    prompts = config.build_prompts(session) if config.build_prompts is not None else None
    return tools, prompts


__all__ = ["DefaultHeadlessBuild", "InMemoryHeadlessBuild", "resolve_agent_ports"]
