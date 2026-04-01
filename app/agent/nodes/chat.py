"""Chat branch nodes - routing, LLM response, and tool execution."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from app.agent.prompts import GENERAL_SYSTEM_PROMPT, ROUTER_PROMPT, SYSTEM_PROMPT
from app.agent.state import AgentState, ChatMessage
from app.agent.tools.clients import get_llm_for_tools
from app.agent.tools.tool_actions.github.github_mcp_actions import (
    get_github_file_contents,
    get_github_repository_tree,
    list_github_commits,
    search_github_code,
)
from app.agent.tools.tool_actions.sentry.sentry_actions import (
    get_sentry_issue_details,
    list_sentry_issue_events,
    search_sentry_issues,
)
from app.agent.tools.tool_actions.tracer.tracer_jobs import (
    get_failed_jobs,
    get_failed_tools,
)
from app.agent.tools.tool_actions.tracer.tracer_logs import get_error_logs
from app.agent.tools.tool_actions.tracer.tracer_metrics import (
    get_batch_statistics,
    get_host_metrics,
)
from app.agent.tools.tool_actions.tracer.tracer_runs import (
    fetch_failed_run,
    get_tracer_run,
    get_tracer_tasks,
)

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

CHAT_TOOLS: list[StructuredTool] = [
    StructuredTool.from_function(fn, return_direct=False) for fn in _CHAT_FUNCTIONS
]

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


# ── Chat LLM (LangChain ChatAnthropic for real-time streaming) ──────────

_chat_llm: ChatAnthropic | None = None
_chat_llm_with_tools: ChatAnthropic | None = None


def _get_chat_llm(*, with_tools: bool = False) -> ChatAnthropic:
    """Get a LangChain ChatAnthropic for chat nodes (supports streaming)."""
    global _chat_llm, _chat_llm_with_tools

    if with_tools:
        if _chat_llm_with_tools is None:
            from app.config import ANTHROPIC_TOOLCALL_MODEL, DEFAULT_MAX_TOKENS

            tool_model = (
                (os.getenv("ANTHROPIC_TOOLCALL_MODEL") or "").strip()
                or (os.getenv("ANTHROPIC_REASONING_MODEL") or "").strip()
                or (os.getenv("ANTHROPIC_MODEL") or "").strip()
                or ANTHROPIC_TOOLCALL_MODEL
            )
            base = ChatAnthropic(  # type: ignore[call-arg]
                model=tool_model,
                max_tokens=DEFAULT_MAX_TOKENS,
                streaming=True,
            )
            _chat_llm_with_tools = base.bind_tools(CHAT_TOOLS)  # type: ignore[assignment]
        return _chat_llm_with_tools  # type: ignore[return-value]

    if _chat_llm is None:
        from app.config import ANTHROPIC_REASONING_MODEL, DEFAULT_MAX_TOKENS

        reasoning_model = (
            (os.getenv("ANTHROPIC_REASONING_MODEL") or "").strip()
            or (os.getenv("ANTHROPIC_MODEL") or "").strip()
            or ANTHROPIC_REASONING_MODEL
        )
        _chat_llm = ChatAnthropic(  # type: ignore[call-arg]
            model=reasoning_model,
            max_tokens=DEFAULT_MAX_TOKENS,
            streaming=True,
        )
    return _chat_llm


# ── Node functions ───────────────────────────────────────────────────────


def router_node(state: AgentState) -> dict[str, Any]:
    """Route chat messages by intent."""
    msgs = _normalize_messages(list(state.get("messages", [])))
    if not msgs or msgs[-1].get("role") != "user":
        return {"route": "general"}

    response = get_llm_for_tools().invoke([
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": str(msgs[-1].get("content", ""))},
    ])
    route = str(response.content).strip().lower()
    return {"route": route if route in ("tracer_data", "general") else "general"}


def chat_agent_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:  # noqa: ARG001
    """Chat agent with tools for Tracer data queries.

    Uses ChatAnthropic with bound tools. The LLM can make tool_calls
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


def general_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:  # noqa: ARG001
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
