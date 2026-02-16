"""Unified agent pipeline - handles both chat and investigation modes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat_tools import CHAT_TOOLS
from app.agent.nodes import (
    node_build_context,
    node_diagnose_root_cause,
    node_extract_alert,
    node_frame_problem,
    node_plan_actions,
    node_publish_findings,
)
from app.agent.nodes.investigate.node import node_investigate
from app.agent.routing import should_continue_investigation
from app.agent.state import AgentState, ChatMessage, make_initial_state
from app.agent.tools.clients import get_llm

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


SYSTEM_PROMPT = """You are a pipeline debugging assistant for Tracer.
You help users understand and debug their bioinformatics pipelines.

You have access to tools that can query Tracer APIs for pipeline runs, tasks, logs,
metrics, and job information. Use these tools when users ask about their pipelines.

For general questions about bioinformatics or pipeline best practices, answer directly
without using tools.

Always respond in clear markdown."""

ROUTER_PROMPT = """Classify the user message:
- "tracer_data" if asking about pipelines, runs, logs, metrics, failures, or debugging
- "general" for general questions, greetings, or best practices

Respond with ONLY: tracer_data or general"""


# ── Chat LLM (LangChain ChatAnthropic for real-time streaming) ──────────
_chat_llm: ChatAnthropic | None = None
_chat_llm_with_tools: ChatAnthropic | None = None


def _get_chat_llm(*, with_tools: bool = False) -> ChatAnthropic:
    """Get a LangChain ChatAnthropic for chat nodes (supports streaming)."""
    global _chat_llm, _chat_llm_with_tools

    if with_tools:
        if _chat_llm_with_tools is None:
            base = ChatAnthropic(  # type: ignore[call-arg]
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                max_tokens=4096,
                streaming=True,
            )
            _chat_llm_with_tools = base.bind_tools(CHAT_TOOLS)  # type: ignore[assignment]
        return _chat_llm_with_tools  # type: ignore[return-value]

    if _chat_llm is None:
        _chat_llm = ChatAnthropic(  # type: ignore[call-arg]
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=4096,
            streaming=True,
        )
    return _chat_llm


def _merge_state(state: AgentState, updates: dict[str, Any]) -> None:
    if not updates:
        return
    state_any = cast(dict[str, Any], state)
    for key, value in updates.items():
        if key == "messages":
            messages = list(state_any.get("messages", []))
            if isinstance(value, list):
                messages.extend(value)
            else:
                messages.append(value)
            state_any["messages"] = messages
            continue
        state_any[key] = value


def _extract_auth(state: AgentState, config: RunnableConfig) -> dict[str, str]:
    """Extract auth context and LangGraph metadata from config."""
    configurable = config.get("configurable", {})
    auth = configurable.get("langgraph_auth_user", {})

    thread_id = configurable.get("thread_id", "") or state.get("thread_id", "")
    run_id = configurable.get("run_id", "") or state.get("run_id", "")
    auth_token = auth.get("token", "") or state.get("_auth_token", "")

    return {
        "org_id": auth.get("org_id") or state.get("org_id", ""),
        "user_id": auth.get("identity") or state.get("user_id", ""),
        "user_email": auth.get("email", ""),
        "user_name": auth.get("full_name", ""),
        "organization_slug": auth.get("organization_slug", ""),
        "thread_id": thread_id,
        "run_id": run_id,
        "_auth_token": auth_token,
    }


# ── Chat mode nodes ─────────────────────────────────────────────────────

def router_node(state: AgentState) -> dict[str, Any]:
    """Route chat messages by intent."""
    msgs = _normalize_messages(list(state.get("messages", [])))
    if not msgs or msgs[-1].get("role") != "user":
        return {"route": "general"}

    response = get_llm().invoke([
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

    # Ensure system prompt is present
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
        msgs = [SystemMessage(content=SYSTEM_PROMPT), *msgs]

    llm = _get_chat_llm(with_tools=False)
    response = llm.invoke(msgs)
    return {"messages": [response]}


def tool_executor_node(state: AgentState) -> dict[str, Any]:
    """Execute tool calls from the last AI message and return ToolMessages."""
    msgs = list(state.get("messages", []))
    if not msgs:
        return {"messages": []}

    # Find the last AI message with tool_calls
    last_ai = None
    for m in reversed(msgs):
        if hasattr(m, "tool_calls") and getattr(m, "tool_calls", None):
            last_ai = m
            break

    if not last_ai or not last_ai.tool_calls:
        return {"messages": []}

    # Build tool lookup
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


def _should_call_tools(state: AgentState) -> str:
    """Check if the last AI message has tool calls that need execution."""
    msgs = list(state.get("messages", []))
    if msgs:
        last = msgs[-1]
        if hasattr(last, "tool_calls") and getattr(last, "tool_calls", None):
            return "call_tools"
    return "done"


# ── Standalone runners (for testing/CLI) ─────────────────────────────────

def run_chat(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
    """Run chat routing + response without LangGraph (for testing)."""
    cfg = config or {"configurable": {}}
    _merge_state(state, router_node(state))
    route = state.get("route", "general")
    if route == "tracer_data":
        _merge_state(state, chat_agent_node(state, cfg))
    else:
        _merge_state(state, general_node(state, cfg))
    return state


def _run_investigation_pipeline(state: AgentState) -> AgentState:
    """Run investigation pipeline sequentially without LangGraph."""
    _merge_state(state, node_extract_alert(state))

    # Skip investigation if classified as noise
    if state.get("is_noise"):
        return state

    _merge_state(state, node_build_context(state))
    _merge_state(state, node_frame_problem(state))

    while True:
        _merge_state(state, node_plan_actions(state))
        _merge_state(state, node_investigate(state))
        _merge_state(state, node_diagnose_root_cause(state))
        if should_continue_investigation(state) != "investigate":
            break

    _merge_state(state, node_publish_findings(state))
    return state


def run_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
) -> AgentState:
    """Run investigation pipeline. Pure function: inputs in, state out."""
    initial = make_initial_state(alert_name, pipeline_name, severity, raw_alert=raw_alert)
    return cast(AgentState, _run_investigation_pipeline(initial))


@dataclass
class SimpleAgent:
    def invoke(self, state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        mode = state.get("mode", "investigation")
        if mode == "chat":
            return run_chat(state, config)
        return _run_investigation_pipeline(state)


# ── Graph nodes ──────────────────────────────────────────────────────────

def inject_auth_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Extract auth context from JWT and inject into state."""
    return _extract_auth(state, config)


def _route_by_mode(state: AgentState) -> str:
    """Route based on agent mode. Defaults to chat when mode is not set."""
    return "investigation" if state.get("mode") == "investigation" else "chat"


def _route_chat(state: AgentState) -> str:
    """Route chat messages by intent."""
    return "tracer_data" if state.get("route") == "tracer_data" else "general"


def _route_after_extract(state: AgentState) -> str:
    """Route after alert extraction - skip investigation if noise."""
    return "end" if state.get("is_noise") else "investigate"


def _route_investigation_loop(state: AgentState) -> str:
    """Decide whether to continue investigation loop."""
    return should_continue_investigation(state)


def build_graph(config: Any | None = None) -> CompiledStateGraph:
    """Build and compile the LangGraph agent."""
    _ = config

    graph = StateGraph(AgentState)

    # Auth injection (shared entry for both branches)
    graph.add_node("inject_auth", inject_auth_node)

    # Chat branch nodes
    graph.add_node("router", router_node)
    graph.add_node("chat_agent", chat_agent_node)  # type: ignore[arg-type]
    graph.add_node("general", general_node)  # type: ignore[arg-type]
    graph.add_node("tool_executor", tool_executor_node)

    # Investigation branch nodes
    graph.add_node("extract_alert", node_extract_alert)
    graph.add_node("build_context", node_build_context)
    graph.add_node("frame_problem", node_frame_problem)
    graph.add_node("plan_actions", node_plan_actions)
    graph.add_node("investigate", node_investigate)
    graph.add_node("diagnose", node_diagnose_root_cause)
    graph.add_node("publish", node_publish_findings)

    # Entry point - always inject auth first
    graph.set_entry_point("inject_auth")

    # After auth, route by mode
    graph.add_conditional_edges(
        "inject_auth",
        _route_by_mode,
        {"chat": "router", "investigation": "extract_alert"},
    )

    # Chat branch edges
    graph.add_conditional_edges(
        "router",
        _route_chat,
        {"tracer_data": "chat_agent", "general": "general"},
    )

    # After chat_agent: check if there are tool calls to execute
    graph.add_conditional_edges(
        "chat_agent",
        _should_call_tools,
        {"call_tools": "tool_executor", "done": END},
    )

    # After tool execution, loop back to chat_agent for the LLM to process results
    graph.add_edge("tool_executor", "chat_agent")

    # General node goes straight to END (no tools)
    graph.add_edge("general", END)

    # Investigation branch edges - skip if noise
    graph.add_conditional_edges(
        "extract_alert",
        _route_after_extract,
        {"end": END, "investigate": "build_context"},
    )
    graph.add_edge("build_context", "frame_problem")
    graph.add_edge("frame_problem", "plan_actions")
    graph.add_edge("plan_actions", "investigate")
    graph.add_edge("investigate", "diagnose")
    graph.add_conditional_edges(
        "diagnose",
        _route_investigation_loop,
        {"investigate": "plan_actions", "publish": "publish"},
    )
    graph.add_edge("publish", END)

    return graph.compile()


# Pre-compiled for import
agent = SimpleAgent()
graph = build_graph()
