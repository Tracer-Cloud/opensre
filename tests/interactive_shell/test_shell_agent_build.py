"""Shell and gateway builds preserve their sessions' tool capabilities."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from config.constants.capabilities import HOSTED_GATEWAY_CAPABILITY
from core.agent_harness.runtime import AgentBuildConfig
from infrastructure.turn_host.capability_policy import ensure_gateway_capability_policy
from surfaces.interactive_shell.runtime.shell_agent import (
    build_shell_agent,
    shell_agent_build_config,
)
from surfaces.interactive_shell.session import Session


def test_shell_agent_build_omits_capability_policy() -> None:
    config = shell_agent_build_config()
    assert config.apply_capability_policy is None
    assert config.build_tools is not None
    assert config.build_prompts is not None


def test_gateway_policy_withholds_account_tools_and_keeps_the_rest() -> None:
    session = Session()
    before = dict(session.available_capabilities)
    ensure_gateway_capability_policy(session)
    assert session.available_capabilities[HOSTED_GATEWAY_CAPABILITY] == ()
    remaining = dict(session.available_capabilities)
    remaining.pop(HOSTED_GATEWAY_CAPABILITY)
    assert remaining == before


def test_build_shell_agent_keeps_existing_capabilities() -> None:
    session = Session()
    session.available_capabilities["task_cancel"] = ("cancel",)
    build_shell_agent(session, Console(file=io.StringIO(), force_terminal=False))
    assert session.available_capabilities["task_cancel"] == ("cancel",)


def test_build_shell_agent_applies_capability_policy_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []
    base = shell_agent_build_config()

    def _config(*, request_exit: Any = None) -> AgentBuildConfig:
        _ = request_exit
        return AgentBuildConfig(
            build_tools=base.build_tools,
            build_prompts=base.build_prompts,
            error_reporter=base.error_reporter,
            apply_capability_policy=seen.append,
        )

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.shell_agent.shell_agent_build_config",
        _config,
    )
    session = Session()
    build_shell_agent(session, Console(file=io.StringIO(), force_terminal=False))
    assert seen == [session]
