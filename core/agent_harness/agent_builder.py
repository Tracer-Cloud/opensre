"""Shared factory for building runtime :class:`~core.agent.Agent` instances.

Each agent harness surface (action, evidence, gateway) assembles its per-turn
configuration in a surface-specific factory and hands it to :func:`build_agent`,
the single construction site for :class:`~core.agent.Agent` across surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from core.agent import Agent
from core.agent.goals import Goal
from core.agent.run_io import ReplyStreamPolicy
from core.events import RuntimeEventCallback
from core.llm.types import AgentLLMClient, ResolvedIntegrations
from core.provider import ProviderHooks
from core.tool.contracts import RuntimeTool
from core.tool.execution import ToolExecutionHooks

RuntimeToolT = TypeVar("RuntimeToolT", bound=RuntimeTool)


@dataclass(frozen=True)
class AgentConfig(Generic[RuntimeToolT]):  # noqa: UP046
    """Immutable per-turn config the runtime :class:`Agent` needs to construct.

    Surfaces assemble one of these and hand it to :func:`build_agent`.
    """

    llm: AgentLLMClient
    system: str
    tools: tuple[RuntimeToolT, ...]
    resolved_integrations: ResolvedIntegrations
    max_iterations: int
    max_stagnant_iterations: int | None = None
    tool_resources: dict[str, Any] = field(default_factory=dict)
    tool_hooks: ToolExecutionHooks | None = None
    provider_hooks: ProviderHooks | None = None
    on_runtime_event: RuntimeEventCallback | None = None
    goal: Goal | None = None
    reply_stream_policy: ReplyStreamPolicy | None = None


def build_agent(  # noqa: UP047
    config: AgentConfig[RuntimeToolT],
) -> Agent[RuntimeToolT]:
    """Construct a runtime :class:`Agent` from an :class:`AgentConfig`.

    This is the single place :class:`Agent` is instantiated across the
    harness — surfaces call it after building their config.
    """
    return Agent[RuntimeToolT](
        llm=config.llm,
        system=config.system,
        tools=config.tools,
        resolved_integrations=config.resolved_integrations,
        max_iterations=config.max_iterations,
        max_stagnant_iterations=config.max_stagnant_iterations,
        tool_resources=config.tool_resources,
        tool_hooks=config.tool_hooks,
        provider_hooks=config.provider_hooks,
        on_runtime_event=config.on_runtime_event,
        goal=config.goal,
        reply_stream_policy=config.reply_stream_policy,
    )


__all__ = ["AgentConfig", "build_agent"]
