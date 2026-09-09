"""Input and output data types for the ReAct loop.

``AgentRunInput`` is the resolved per-run input ``Agent.run`` assembles and
hands to ``run_react_loop``; ``AgentRunResult`` is what the loop returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from core.llm.types import ToolCall
from core.messages import MessageMapper, RuntimeMessage, RuntimeMessageLike
from core.tool.contracts import RuntimeTool
from core.tool.execution import ToolExecutionResult


@dataclass
class AgentRunResult:
    """Outcome of :func:`core.agent.react_loop.run_react_loop` (returned as-is by ``Agent.run``).

    ``messages`` is the full conversation, ``final_text`` is the assistant's
    last no-tool-call turn, ``executed`` is the historical ordered list of raw
    tool payloads, and ``tool_results`` contains the structured runtime results.
    """

    messages: list[RuntimeMessage]
    final_text: str
    executed: list[tuple[ToolCall, Any]] = field(default_factory=list)
    tool_results: list[tuple[ToolCall, ToolExecutionResult]] = field(default_factory=list)
    terminated_by_tool: bool = False
    #: Host cooperative cancel (``console.cancel_requested``), not a tool terminate.
    cancelled: bool = False
    hit_iteration_cap: bool = False
    llm_iterations_used: int = 0
    final_system_prompt: str = ""
    #: Provider-reported usage summed over the run's model calls (0 when unreported).
    input_tokens: int = 0
    output_tokens: int = 0
    """System prompt sent to the LLM on the last request (post-hook), for debugging."""


@dataclass
class AgentRunInput[RuntimeToolT: RuntimeTool]:
    """Resolved, per-run inputs the loop needs — assembled once by ``Agent.run``."""

    llm: Any
    system: str
    tools: list[RuntimeToolT]
    resolved: dict[str, Any]
    tool_resources: dict[str, Any]
    max_iterations: int
    messages: list[RuntimeMessage]
    max_stagnant_iterations: int | None = None

    @classmethod
    def from_runtime_request(cls, request: Any, *, llm: Any) -> AgentRunInput[RuntimeToolT]:
        """Build from a validated per-turn ``AgentRuntimeRequest`` and a resolved ``llm``.

        ``request`` is duck-typed so this DTO stays free of ``agent_harness``:
        the caller (``Agent.run``) validates it before handing it here.
        """
        messages = request.runtime_messages()
        render_system_prompt = getattr(request, "render_system_prompt", None)
        system = (
            render_system_prompt() if callable(render_system_prompt) else str(request.system_prompt)
        )
        return cls(
            llm=llm,
            system=system,
            tools=list(request.active_tools),
            resolved=dict(request.resolved_integrations or {}),
            tool_resources=dict(getattr(request, "tool_resources", {}) or {}),
            max_iterations=request.max_iterations,
            messages=messages,
            max_stagnant_iterations=getattr(request, "max_stagnant_iterations", None),
        )

    @classmethod
    def from_messages(
        cls,
        messages: Sequence[RuntimeMessageLike],
        *,
        llm: Any,
        system: str,
        tools: Sequence[RuntimeToolT] | None,
        resolved: dict[str, Any] | None,
        tool_resources: dict[str, Any],
        max_iterations: int,
        max_stagnant_iterations: int | None = None,
    ) -> AgentRunInput[RuntimeToolT]:
        """Build from raw messages and a caller's construction-time config."""
        return cls(
            llm=llm,
            system=system,
            tools=list(tools) if tools is not None else [],
            resolved=dict(resolved) if resolved is not None else {},
            tool_resources=dict(tool_resources),
            max_iterations=max_iterations,
            messages=MessageMapper.to_runtime_messages(messages),
            max_stagnant_iterations=max_stagnant_iterations,
        )


__all__ = ["AgentRunInput", "AgentRunResult"]
