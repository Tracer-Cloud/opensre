"""A channel brings its own ports; the gateway still owns the turn.

Chat transports need nothing beyond the defaults. The interactive shell needs
its own tool provider, prompt grounding, console progress and error reporter —
without them it loses CLI-reference grounding, progress lines, and the REPL
slash ports whose ``tty_interactive()`` defers an interactive picker instead of
running it against a live prompt.

So the pool accepts what a channel supplies and keeps deciding everything else:
which agent is reused, when the capability policy applies, how live output is
bound.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from rich.console import Console

from config.constants.capabilities import (
    HOSTED_GATEWAY_CAPABILITY,
    MCP_GATEWAY_MUTATION_CAPABILITY,
)
from core.agent_harness.runtime import AgentBuildConfig
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from infrastructure.turn_host.bindable_output import BindableOutput
from infrastructure.turn_host.session_agents import SessionAgentPool
from tests.shared.default_headless_build_stub import default_headless_build_stub

_LOGGER = logging.getLogger("channel.ports.test")


def _session() -> SessionCore:
    return SessionCore(store=InMemorySessionStore())


def _null_tools(*_args: Any, **_kwargs: Any) -> Any:
    from core.agent_harness.turns.headless_adapters import NullToolProvider

    return NullToolProvider()


def test_a_host_that_supplies_nothing_gets_the_chat_defaults() -> None:
    # Arrange — how every chat transport constructs the pool today
    session = _session()
    pool = SessionAgentPool(console=Console(force_terminal=False))

    # Act
    agent = pool.agent_for(session=session, output=BindableOutput(), logger=_LOGGER)

    # Assert — chat defaults withhold tools that need this machine's account token
    assert agent is not None
    assert session.available_capabilities == {
        HOSTED_GATEWAY_CAPABILITY: (),
        MCP_GATEWAY_MUTATION_CAPABILITY: (),
    }


def test_a_host_supplies_its_own_tools_prompts_and_gather() -> None:
    # Arrange — record which of the channel's builders the pool actually used
    used: list[str] = []

    def build_tools(session: Any, console: Any, logger: Any, observer: Any) -> Any:
        _ = (session, console, logger, observer)
        used.append("tools")
        return _null_tools()

    def build_prompts(session: Any) -> Any:
        _ = session
        used.append("prompts")
        from core.agent_harness.spi.defaults import DefaultPromptContextProvider

        return DefaultPromptContextProvider(session)

    pool = SessionAgentPool(
        console=Console(force_terminal=False),
        agent_build=AgentBuildConfig(
            build_tools=build_tools,
            build_prompts=build_prompts,
        ),
    )

    # Act
    pool.agent_for(session=_session(), output=BindableOutput(), logger=_LOGGER)

    # Assert — the channel's builders ran, not the gateway defaults
    assert sorted(used) == ["prompts", "tools"]


def test_agent_reuse_stays_the_pool_decision() -> None:
    """Whatever a channel supplies, caching is not its call."""
    # Arrange
    builds: list[str] = []

    def build_tools(session: Any, console: Any, logger: Any, observer: Any) -> Any:
        _ = (session, console, logger, observer)
        builds.append("build")
        return _null_tools()

    pool = SessionAgentPool(
        console=Console(force_terminal=False),
        agent_build=AgentBuildConfig(build_tools=build_tools),
    )
    session = _session()

    # Act — two turns of one session
    first = pool.agent_for(session=session, output=BindableOutput(), logger=_LOGGER)
    second = pool.agent_for(session=session, output=BindableOutput(), logger=_LOGGER)

    # Assert — one agent, built once
    assert first is second
    assert builds == ["build"]


def test_default_path_preserves_existing_capabilities() -> None:
    session = _session()
    session.available_capabilities["llm_provider"] = ("switch",)
    SessionAgentPool(console=Console(force_terminal=False)).agent_for(
        session=session, output=BindableOutput(), logger=_LOGGER
    )
    assert session.available_capabilities == {
        "llm_provider": ("switch",),
        HOSTED_GATEWAY_CAPABILITY: (),
        MCP_GATEWAY_MUTATION_CAPABILITY: (),
    }


def test_empty_config_leaves_capabilities_unchanged() -> None:
    session = _session()
    SessionAgentPool(
        console=Console(force_terminal=False),
        agent_build=AgentBuildConfig(),
    ).agent_for(session=session, output=BindableOutput(), logger=_LOGGER)
    assert "llm_provider" not in session.available_capabilities


def test_host_can_replace_capability_policy() -> None:
    seen: list[object] = []
    session = _session()
    SessionAgentPool(
        console=Console(force_terminal=False),
        agent_build=AgentBuildConfig(apply_capability_policy=seen.append),
    ).agent_for(session=session, output=BindableOutput(), logger=_LOGGER)
    assert seen == [session]
    assert "llm_provider" not in session.available_capabilities


def test_error_reporter_and_surface_reach_default_headless_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _build(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _null_tools()

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(_build),
    )
    reporter = object()
    SessionAgentPool(
        console=Console(force_terminal=False),
        agent_build=AgentBuildConfig(error_reporter=reporter),
    ).agent_for(session=_session(), output=BindableOutput(), logger=_LOGGER)
    assert captured["surface"] == "gateway"
    assert captured["error_reporter"] is reporter


def test_default_tools_still_receive_slash_ports_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _tools(*_args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return _null_tools()

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultToolProvider",
        _tools,
    )
    factory = object()
    SessionAgentPool(
        console=Console(force_terminal=False),
        slash_ports_factory=factory,  # type: ignore[arg-type]
    ).agent_for(session=_session(), output=BindableOutput(), logger=_LOGGER)
    assert captured["slash_ports_factory"] is factory
