"""How the gateway wants a chat turn's agent built.

The turn host sits below the tool tier, so it cannot read the tool registry or
the subprocess presenter itself. The gateway can see both, and states here what
a chat turn gets: the withheld capabilities, the live tool wording, and the
headless subprocess rendering.
"""

from __future__ import annotations

from functools import partial

from core.agent_harness.runtime import AgentBuildConfig
from infrastructure.turn_host.capability_policy import ensure_gateway_capability_policy
from tools.interactive_shell.subprocess_presenter import (
    headless_subprocess_presenter_factory,
)
from tools.registry import describe_registered_tool


def chat_agent_build_config(*, hosts_scheduler: bool = False) -> AgentBuildConfig:
    """The chat defaults: gateway withholds, registry tool wording, headless presenter."""
    return AgentBuildConfig(
        apply_capability_policy=partial(
            ensure_gateway_capability_policy, hosts_scheduler=hosts_scheduler
        ),
        describe_tool=describe_registered_tool,
        subprocess_presenter_factory=headless_subprocess_presenter_factory,
    )


__all__ = ["chat_agent_build_config"]
