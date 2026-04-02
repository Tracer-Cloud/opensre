"""Chat branch nodes - routing, LLM response, and tool execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from importlib import import_module
from typing import Any, TypeAlias
from typing import cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import StructuredTool

from app.config import (
    ANTHROPIC_TOOLCALL_MODEL,
    ANTHROPIC_REASONING_MODEL,
    DEFAULT_MAX_TOKENS,
    OPENAI_REASONING_MODEL,
    OPENAI_TOOLCALL_MODEL,
)
from app.integrations.clients import get_llm_for_tools
from app.prompts import GENERAL_SYSTEM_PROMPT, ROUTER_PROMPT, SYSTEM_PROMPT
from app.state import AgentState, ChatMessage
from app.tools.base import BaseTool
from app.tools.GitHubCommitsTool import list_github_commits
from app.tools.GitHubFileContentsTool import get_github_file_contents
from app.tools.GitHubRepositoryTreeTool import get_github_repository_tree
from app.tools.GitHubSearchCodeTool import search_github_code
from app.tools.SentryIssueDetailsTool import get_sentry_issue_details
from app.tools.SentryIssueEventsTool import list_sentry_issue_events
from app.tools.SentrySearchIssuesTool import search_sentry_issues
from app.tools.TracerBatchStatisticsTool import get_batch_statistics
from app.tools.TracerErrorLogsTool import get_error_logs
from app.tools.TracerFailedJobsTool import get_failed_jobs
from app.tools.TracerFailedRunTool import fetch_failed_run
from app.tools.TracerFailedToolsTool import get_failed_tools
from app.tools.TracerHostMetricsTool import get_host_metrics
from app.tools.TracerRunTool import get_tracer_run
from app.tools.TracerTasksTool import get_tracer_tasks
from app.utils.cfg_helpers import CfgHelpers

_CHAT_FUNCTIONS: list[Callable[..., Any]] = [
    fetch_failed_run,
    get_tracer_run,
    get_tracer_tasks,
    get_failed_jobs,
    get_failed_tools,
    get_error_logs,
    get_batch_statistics,
    get_host_metrics,
    search_github_code,
    get_github_file_contents,
    get_github_repository_tree,
    list_github_commits,
    search_sentry_issues,
    get_sentry_issue_details,
    list_sentry_issue_events,
]


def _to_structured_tool(fn: Callable[..., Any] | BaseTool) -> StructuredTool:
    """Build a StructuredTool from a plain callable or a BaseTool instance."""
    if isinstance(fn, BaseTool):
        return StructuredTool.from_function(
            func=fn.run,  # type: ignore[attr-defined]
            name=fn.name,
            description=fn.description,
            return_direct=False,
        )
    return StructuredTool.from_function(fn, return_direct=False)


CHAT_TOOLS: list[StructuredTool] = [_to_structured_tool(fn) for fn in _CHAT_FUNCTIONS]

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

ToolEnabledChatModel: TypeAlias = Runnable[object, object]

_chat_llm: BaseChatModel | None = None
_chat_llm_with_tools: ToolEnabledChatModel | None = None
_chat_llm_provider: str | None = None
_chat_llm_with_tools_provider: str | None = None


def _resolve_models(provider: str) -> tuple[str, str]:
    match provider:
        case "openai":
            tool_model = CfgHelpers.first_env_or_default(
                env_keys=(
                    "OPENAI_TOOLCALL_MODEL",
                    "OPENAI_REASONING_MODEL",
                    "OPENAI_MODEL",
                ),
                default=OPENAI_TOOLCALL_MODEL,
            )
            reasoning_model = CfgHelpers.first_env_or_default(
                env_keys=(
                    "OPENAI_REASONING_MODEL",
                    "OPENAI_MODEL",
                ),
                default=OPENAI_REASONING_MODEL,
            )
            return tool_model, reasoning_model
        case "anthropic":
            tool_model = CfgHelpers.first_env_or_default(
                env_keys=(
                    "ANTHROPIC_TOOLCALL_MODEL",
                    "ANTHROPIC_REASONING_MODEL",
                    "ANTHROPIC_MODEL",
                ),
                default=ANTHROPIC_TOOLCALL_MODEL,
            )
            reasoning_model = CfgHelpers.first_env_or_default(
                env_keys=(
                    "ANTHROPIC_REASONING_MODEL",
                    "ANTHROPIC_MODEL",
                ),
                default=ANTHROPIC_REASONING_MODEL,
            )
            return tool_model, reasoning_model
        case _:
            raise ValueError(f"Unsupported chat model provider: {provider}")


def _build_chat_model(*, provider: str, model_name: str) -> BaseChatModel:
    """Lazy-build chat model depending on provider and model name.
    Args:
        provider (str): The resolved provider name.
        model_name (str): The model name.
    Returns:
        BaseChatModel: The chat model.
    """
    match provider:
        case "openai":
            chat_openai_cls = cast(
                type[BaseChatModel],
                getattr(import_module("langchain_openai"), "ChatOpenAI"),
            )
            return chat_openai_cls(
                model=model_name,
                max_tokens=DEFAULT_MAX_TOKENS,
                streaming=True,
            )
        case "anthropic":
            chat_anthropic_cls = cast(
                type[BaseChatModel],
                getattr(import_module("langchain_anthropic"), "ChatAnthropic"),
            )
            return chat_anthropic_cls(
                model=model_name,
                max_tokens=DEFAULT_MAX_TOKENS,
                streaming=True,
            )
        case _:
            raise ValueError(f"Unsupported chat model provider: {provider}")


def _get_chat_llm(*, with_tools: bool = False) -> BaseChatModel | ToolEnabledChatModel:
    """Get chat model used by chat nodes.
    Args:
        with_tools (bool): Whether to include tools in the chat model.
    Returns:
        BaseChatModel | ToolEnabledChatModel: The base chat model.
    """
    global _chat_llm, _chat_llm_with_tools, _chat_llm_provider, _chat_llm_with_tools_provider
    provider = CfgHelpers.resolve_llm_provider()
    tool_model, reasoning_model = _resolve_models(provider)

    if with_tools:
        # None = first-time build
        # inequality = possible cache invalidation, provider changed therefore rebuild is needed
        if _chat_llm_with_tools is None or _chat_llm_with_tools_provider != provider:
            base = _build_chat_model(provider=provider, model_name=tool_model)
            _chat_llm_with_tools = base.bind_tools(CHAT_TOOLS)  # type: ignore[assignment]
            _chat_llm_with_tools_provider = provider
        return _chat_llm_with_tools  # type: ignore[return-value]

    if _chat_llm is None or _chat_llm_provider != provider:
        _chat_llm = _build_chat_model(provider=provider, model_name=reasoning_model)
        _chat_llm_provider = provider
    return _chat_llm


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


def chat_agent_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:  # noqa: ARG001
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


def general_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:  # noqa: ARG001
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

    tool_map = {t.name: t for t in CHAT_TOOLS}

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
        except Exception as e:
            result = json.dumps({"error": str(e)})

        tool_messages.append(
            ToolMessage(content=result, tool_call_id=tool_id, name=tool_name)
        )

    return {"messages": tool_messages}
