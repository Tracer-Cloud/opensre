"""Agent runtime: build and run the agent that executes turns.

The headless agent, the two HeadlessAgent builds it is built on (``DefaultHeadlessBuild``,
``InMemoryHeadlessBuild``),
the default tool provider a host configures with its tool factories, the
per-turn binding, the turn plan the runtime hands to stages, the action-turn
runner, and the ReAct engine itself
(``AgentConfig``/``build_agent`` and the default LLM factories).
Importing this loads ``core.agent``;
hosts import it when a turn is dispatched, not at boot.
"""

from __future__ import annotations

from core.agent_harness.agent_build_config import AgentBuildConfig, DescribeTool
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.llm_resolution import (
    agent_llm_is_cli_backed,
    default_llm_factory,
    default_reasoning_llm_factory,
)
from core.agent_harness.ports import TurnBinding
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.action_driver import ActionTurnRunner
from core.agent_harness.turns.headless_agent import AgentBusyError, HeadlessAgent
from core.agent_harness.turns.headless_build import (
    DefaultHeadlessBuild,
    InMemoryHeadlessBuild,
    resolve_agent_ports,
)
from core.agent_harness.turns.turn_plan import TurnPlan

__all__ = [
    "ActionTurnRunner",
    "AgentBuildConfig",
    "AgentBusyError",
    "AgentConfig",
    "DefaultHeadlessBuild",
    "DescribeTool",
    "DefaultToolProvider",
    "HeadlessAgent",
    "InMemoryHeadlessBuild",
    "TurnBinding",
    "TurnPlan",
    "agent_llm_is_cli_backed",
    "build_agent",
    "default_llm_factory",
    "default_reasoning_llm_factory",
    "resolve_agent_ports",
]
