"""Build the interactive shell's agent with DefaultHeadlessBuild.

The shell is a host: it supplies :class:`AgentBuildConfig` (tools, prompts,
error reporter) and omits capability policy so gateway-chat withholds
do not run. Construction still goes through :class:`DefaultHeadlessBuild` — the same
family the gateway pool uses — so the shell keeps llm_provider / task_cancel
and REPL slash / TTY paint.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast

from rich.console import Console

from core.agent_harness import OutputSink
from core.agent_harness.ports import SessionState, ToolEventObserver
from core.agent_harness.runtime import (
    AgentBuildConfig,
    DefaultHeadlessBuild,
    DefaultToolProvider,
    HeadlessAgent,
    resolve_agent_ports,
)
from core.agent_harness.spi.defaults import DefaultPromptContextProvider
from surfaces.interactive_shell.runtime.agent_harness_adapters import (
    ShellErrorReporter,
    resolve_output_sink,
)
from surfaces.interactive_shell.runtime.llm_provider_adapter import repl_llm_provider_ports
from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports
from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import (
    ReplSubprocessPresenter,
)
from surfaces.interactive_shell.runtime.task_cancel_adapter import repl_task_cancel_ports
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.action_rendering import ActionRenderObserver


def _subprocess_presenter_factory(
    session: Session,
    console: Console,
    confirm_fn: Callable[[str], str] | None,
    is_tty: bool | None,
    action_already_listed: bool,
) -> ReplSubprocessPresenter:
    return ReplSubprocessPresenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    )


def _observer_factory(session: Session, console: Console) -> Callable[[str], Any]:
    def observer_factory(message: str) -> Any:
        return ActionRenderObserver(session=session, console=console, message=message)

    return observer_factory


def _shell_prompts(session: SessionState) -> DefaultPromptContextProvider:
    """The shell's turns run on the interactive-shell prompt surface."""
    return DefaultPromptContextProvider(session, surface="interactive_shell")


def shell_agent_build_config(
    *,
    request_exit: Callable[[], None] | None = None,
) -> AgentBuildConfig:
    """REPL wiring: shell tools on the interactive-shell prompt surface; no withholds."""

    def build_tools(
        session: SessionState,
        console: Console,
        _logger: logging.Logger,
        _observer: ToolEventObserver | None,
    ) -> DefaultToolProvider:
        # BuildTools' Protocol declares the base SessionState (contravariance
        # requires accepting at least as wide a type); this closure is only
        # ever wired up below with a real interactive-shell Session.
        return shell_tool_provider(cast(Session, session), console, request_exit=request_exit)

    return AgentBuildConfig(
        build_tools=build_tools,
        build_prompts=_shell_prompts,
        error_reporter=ShellErrorReporter(),
    )


def shell_tool_provider(
    session: Session,
    console: Console,
    *,
    request_exit: Callable[[], None] | None = None,
) -> DefaultToolProvider:
    """The shell's tools: the harness provider with the shell's port factories."""
    return DefaultToolProvider(
        session,
        console,
        request_exit=request_exit,
        observer_factory=_observer_factory(session, console),
        subprocess_presenter_factory=_subprocess_presenter_factory,
        llm_provider_ports_factory=repl_llm_provider_ports,
        task_cancel_ports_factory=repl_task_cancel_ports,
        slash_ports_factory=repl_slash_ports,
    )


def build_shell_agent(
    session: Session,
    console: Console,
    *,
    output: OutputSink | None = None,
    request_exit: Callable[[], None] | None = None,
) -> HeadlessAgent:
    """One shell agent from :func:`shell_agent_build_config`; per-turn values via ``bind_turn``."""
    config = shell_agent_build_config(request_exit=request_exit)
    if config.apply_capability_policy is not None:
        config.apply_capability_policy(session)
    tools, prompts = resolve_agent_ports(
        config,
        session=session,
        console=console,
        logger=logging.getLogger("opensre.interactive_shell"),
    )
    return DefaultHeadlessBuild(
        session=session,
        output=resolve_output_sink(console, output, session),
        console=console,
        surface="interactive_shell",
        error_reporter=config.error_reporter,
    ).agent(tools=tools, prompts=prompts)


__all__ = [
    "build_shell_agent",
    "shell_agent_build_config",
    "shell_tool_provider",
]
