"""Chat branch nodes - routing, LLM response, and tool execution."""

from __future__ import annotations

import json
from importlib import import_module
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import StructuredTool

from app.config import ANTHROPIC_LLM_CONFIG, DEFAULT_MAX_TOKENS, OPENAI_LLM_CONFIG
from app.constants.prompts import GENERAL_SYSTEM_PROMPT, ROUTER_PROMPT, SYSTEM_PROMPT
from app.services import get_llm_for_tools
from app.state import AgentState, ChatMessage
from app.tools.registered_tool import RegisteredTool
from app.tools.registry import get_registered_tools
from app.utils.cfg_helpers import CfgHelpers


def _to_structured_tool(tool: RegisteredTool) -> StructuredTool:
    """Build a StructuredTool from the canonical registered tool runtime."""
    return StructuredTool.from_function(
        func=tool.run,
        name=tool.name,
        description=tool.description,
        return_direct=False,
    )


def get_chat_tools() -> list[StructuredTool]:
    return [_to_structured_tool(tool) for tool in get_registered_tools("chat")]

# LangChain type -> ChatMessage role mapping
_TYPE_TO_ROLE: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
}


def _normalize_messages(msgs: list[Any]) -> list[ChatMessage]:
    """Normalize messages from LangChain format to plain ChatMessage dicts."""
    result: list[ChatMessage] = []
    for m in msgs:
        if hasattr(m, "type") and hasattr(m, "content"):
            role = _TYPE_TO_ROLE.get(m.type, "user")
            result.append({"role": role, "content": str(m.content)})  # type: ignore[typeddict-item]
            continue
        if not isinstance(m, dict):
            continue
        if "role" in m:
            result.append(m)  # type: ignore[arg-type]
            continue
        if "type" in m:
            role = _TYPE_TO_ROLE.get(m["type"], "user")
            result.append({"role": role, "content": str(m.get("content", ""))})  # type: ignore[typeddict-item]
            continue
        result.append(m)  # type: ignore[arg-type]
    return result


# ── Chat LLM ─────────────────────────────────────────────────────────────

type ToolEnabledChatModel = Runnable[object, object]

_chat_llm_cache: dict[str, BaseChatModel] = {}
_chat_llm_with_tools_cache: dict[str, ToolEnabledChatModel] = {}


def _resolve_models(provider: str) -> tuple[str, str]:
    """Resolve tool and reasoning model names for the active provider."""
    if provider == "openai":
        return (
            CfgHelpers.first_env_or_default(
                env_keys=(
                    "OPENAI_TOOLCALL_MODEL",
                    "OPENAI_REASONING_MODEL",
                    "OPENAI_MODEL",
                ),
                default=OPENAI_LLM_CONFIG.toolcall_model,
            ),
            CfgHelpers.first_env_or_default(
                env_keys=("OPENAI_REASONING_MODEL", "OPENAI_MODEL"),
                default=OPENAI_LLM_CONFIG.reasoning_model,
            ),
        )
    if provider == "anthropic":
        return (
            CfgHelpers.first_env_or_default(
                env_keys=(
                    "ANTHROPIC_TOOLCALL_MODEL",
                    "ANTHROPIC_REASONING_MODEL",
                    "ANTHROPIC_MODEL",
                ),
                default=ANTHROPIC_LLM_CONFIG.toolcall_model,
            ),
            CfgHelpers.first_env_or_default(
                env_keys=("ANTHROPIC_REASONING_MODEL", "ANTHROPIC_MODEL"),
                default=ANTHROPIC_LLM_CONFIG.reasoning_model,
            ),
        )
    raise ValueError(f"Unsupported chat model provider: {provider}")


def _build_chat_model(*, provider: str, model_name: str) -> BaseChatModel:
    """Lazy-build a provider-specific chat model for the chat nodes."""
    if provider == "openai":
        openai_module = import_module("langchain_openai")
        chat_openai_cls: Any = openai_module.ChatOpenAI
        return cast(
            BaseChatModel,
            chat_openai_cls(
                model=model_name,
                max_tokens=DEFAULT_MAX_TOKENS,
                streaming=True,
            ),
        )
    if provider == "anthropic":
        anthropic_module = import_module("langchain_anthropic")
        chat_anthropic_cls: Any = anthropic_module.ChatAnthropic
        return cast(
            BaseChatModel,
            chat_anthropic_cls(
                model=model_name,
                max_tokens=DEFAULT_MAX_TOKENS,
                streaming=True,
            ),
        )
    raise ValueError(f"Unsupported chat model provider: {provider}")


def _get_chat_llm(*, with_tools: bool = False) -> BaseChatModel | ToolEnabledChatModel:
    """Get the provider-aware chat model used by chat nodes."""
    provider = CfgHelpers.resolve_llm_provider()
    tool_model, reasoning_model = _resolve_models(provider)

    if with_tools:
        cached_tool_model = _chat_llm_with_tools_cache.get(provider)
        if cached_tool_model is None:
            base = _build_chat_model(provider=provider, model_name=tool_model)
            cached_tool_model = cast(ToolEnabledChatModel, base.bind_tools(get_chat_tools()))
            _chat_llm_with_tools_cache[provider] = cached_tool_model
        return cached_tool_model

    cached_reasoning_model = _chat_llm_cache.get(provider)
    if cached_reasoning_model is None:
        cached_reasoning_model = _build_chat_model(
            provider=provider, model_name=reasoning_model
        )
        _chat_llm_cache[provider] = cached_reasoning_model
    return cached_reasoning_model


# ── Node functions ───────────────────────────────────────────────────────


def router_node(state: AgentState) -> dict[str, Any]:
    """Route chat messages by intent."""
    msgs = _normalize_messages(list(state.get("messages", [])))
    if not msgs or msgs[-1].get("role") != "user":
        return {"route": "general"}

    response = get_llm_for_tools().invoke(
        [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": str(msgs[-1].get("content", ""))},
        ]
    )
    route = str(response.content).strip().lower()
    return {"route": route if route in ("tracer_data", "general") else "general"}


def chat_agent_node(state: AgentState, _config: RunnableConfig) -> dict[str, Any]:
    """Chat agent with tools for Tracer data queries.

    Uses the configured provider with bound tools. The LLM can make tool calls
    which will be executed by the tool_executor node.
    """
    msgs = list(state.get("messages", []))

    has_system = any(
        (hasattr(m, "type") and m.type == "system")
        or (isinstance(m, dict) and m.get("type") == "system")
        for m in msgs
    )
    if not has_system:
        msgs = [SystemMessage(content=SYSTEM_PROMPT), *msgs]

    llm = _get_chat_llm(with_tools=True)
    response = llm.invoke(msgs)
    return {"messages": [response]}


def general_node(state: AgentState, _config: RunnableConfig) -> dict[str, Any]:
    """Direct LLM response without tools for general questions."""
    msgs = list(state.get("messages", []))

    has_system = any(
        (hasattr(m, "type") and m.type == "system")
        or (isinstance(m, dict) and m.get("type") == "system")
        for m in msgs
    )
    if not has_system:
        msgs = [SystemMessage(content=GENERAL_SYSTEM_PROMPT), *msgs]

    llm = _get_chat_llm(with_tools=False)
    response = llm.invoke(msgs)
    return {"messages": [response]}


def tool_executor_node(state: AgentState) -> dict[str, Any]:
    """Execute tool calls from the last AI message and return ToolMessages."""
    msgs = list(state.get("messages", []))
    if not msgs:
        return {"messages": []}

    last_ai = None
    for m in reversed(msgs):
        if hasattr(m, "tool_calls") and getattr(m, "tool_calls", None):
            last_ai = m
            break

    if not last_ai or not last_ai.tool_calls:
        return {"messages": []}

    tool_map = {tool.name: tool for tool in get_chat_tools()}

    tool_messages = []
    for tc in last_ai.tool_calls:
        tool_name = tc["name"]
        tool_args = tc.get("args", {})
        tool_id = tc["id"]

        try:
            tool_fn = tool_map.get(tool_name)
            if tool_fn is None:
                result = json.dumps({"error": f"Unknown tool: {tool_name}"})
            else:
                result = tool_fn.invoke(tool_args)
                if not isinstance(result, str):
                    result = json.dumps(result, default=str)
        except (RuntimeError, ValueError, TypeError, KeyError) as e:
            result = json.dumps({"error": str(e)})

        tool_messages.append(
            ToolMessage(content=result, tool_call_id=tool_id, name=tool_name)
        )

    return {"messages": tool_messages}
