"""Shared LLM tool-calling runtime.

Provider-agnostic machinery for running a think → call tools → observe loop:
parallel tool execution, provider-specific message shaping, and context-window
budget enforcement.

The top-level primitive is :class:`~core.agent.Agent`. Surfaces that
previously called ``run_tool_calling_loop`` should instantiate ``Agent``
directly and call ``.run(initial_messages)``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "Agent": "core.agent",
    "AgentRunResult": "core.agent",
    "context_budget_ceiling_for_model": "core.context_budget",
    "enforce_context_budget": "core.context_budget",
    "estimate_message_tokens": "core.context_budget",
    "system_and_tools_overhead": "core.context_budget",
    "trim_lowest_value_tool_pair": "core.context_budget",
    "truncate_content": "core.context_budget",
    "AgentEndEvent": "core.events",
    "AgentStartEvent": "core.events",
    "MessageStartEvent": "core.events",
    "MessageUpdateEvent": "core.events",
    "ProviderRequestEndEvent": "core.events",
    "ProviderRequestStartEvent": "core.events",
    "RuntimeEvent": "core.events",
    "RuntimeEventCallback": "core.events",
    "RuntimeEventKind": "core.events",
    "RuntimeEventType": "core.events",
    "ToolExecutionEndEvent": "core.events",
    "ToolExecutionStartEvent": "core.events",
    "ToolExecutionUpdateEvent": "core.events",
    "TupleEventCallback": "core.events",
    "TurnEndEvent": "core.events",
    "TurnStartEvent": "core.events",
    "runtime_event_from_tuple": "core.events",
    "tuple_payload_from_event": "core.events",
    "BeforeToolCallResult": "core.tool.execution",
    "ToolExecutionHooks": "core.tool.execution",
    "ToolExecutionPatch": "core.tool.execution",
    "ToolExecutionRequest": "core.tool.execution",
    "ToolExecutionResult": "core.tool.execution",
    "execute_tool_calls": "core.tool.execution",
    "execute_tools": "core.tool.execution",
    "public_tool_input": "core.tool.execution",
    "summarise": "core.tool.execution",
    "tool_source": "core.tool.execution",
    "LLMInvokeFailure": "core.llm_invoke_errors",
    "classify_llm_invoke_failure": "core.llm_invoke_errors",
    "AppRuntimeMessage": "core.messages",
    "AssistantRuntimeMessage": "core.messages",
    "MessageMapper": "core.messages",
    "RuntimeMessage": "core.messages",
    "ToolResultRuntimeMessage": "core.messages",
    "UserRuntimeMessage": "core.messages",
    "ProviderHooks": "core.provider",
    "ProviderRequest": "core.provider",
    "resolve_llm_api_key": "core.provider",
    "AgentTool": "core.tool.contracts",
    "AgentToolContext": "core.tool.contracts",
    "AgentToolExecutor": "core.tool.contracts",
    "RuntimeTool": "core.tool.contracts",
    "ToolRole": "core.tool.contracts",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(_EXPORT_MODULES)


__all__ = sorted(_EXPORT_MODULES)
