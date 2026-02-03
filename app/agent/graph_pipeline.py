"""Unified agent graph - handles both chat and investigation modes."""

import os
from typing import Any, Literal, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

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
from app.agent.state import AgentState, make_initial_state
from app.agent.tools.tool_actions import (
    fetch_failed_run_tool,
    get_batch_statistics_tool,
    get_error_logs_tool,
    get_failed_jobs_tool,
    get_failed_tools_tool,
    get_host_metrics_tool,
    get_tracer_run_tool,
    get_tracer_tasks_tool,
)
from app.config import DEFAULT_MAX_TOKENS, DEFAULT_MODEL

SYSTEM_PROMPT = """You are a pipeline debugging assistant for Tracer.
You help users understand and debug their bioinformatics pipelines.

You have access to tools for querying pipeline data, runs, logs, and metrics.
Use these tools when users ask about their pipelines, failed runs, or need debugging help.

For general questions about bioinformatics or pipeline best practices, answer directly."""

ROUTER_PROMPT = """Classify the user message:
- "tracer_data" if asking about pipelines, runs, logs, metrics, failures, or debugging
- "general" for general questions, greetings, or best practices

Respond with ONLY: tracer_data or general"""


def _get_llm() -> ChatAnthropic:
    return ChatAnthropic(  # type: ignore[call-arg]
        model=os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL),
        max_tokens=DEFAULT_MAX_TOKENS,
    )


def _get_tools() -> list:
    return [
        get_tracer_run_tool,
        get_tracer_tasks_tool,
        fetch_failed_run_tool,
        get_failed_jobs_tool,
        get_failed_tools_tool,
        get_error_logs_tool,
        get_batch_statistics_tool,
        get_host_metrics_tool,
    ]


def _extract_auth(state: AgentState, config: RunnableConfig) -> dict[str, str]:
    """Extract auth context from config."""
    auth = config.get("configurable", {}).get("langgraph_auth_user", {})
    return {
        "org_id": auth.get("org_id") or state.get("org_id", ""),
        "user_id": auth.get("identity") or state.get("user_id", ""),
        "user_email": auth.get("email", ""),
        "user_name": auth.get("full_name", ""),
        "organization_slug": auth.get("organization_slug", ""),
    }


# Chat mode nodes
def router_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Route chat messages by intent."""
    _ = config
    msgs = list(state.get("messages", []))
    if not msgs or not isinstance(msgs[-1], HumanMessage):
        return {"route": "general"}

    response = _get_llm().invoke([
        SystemMessage(content=ROUTER_PROMPT),
        HumanMessage(content=str(msgs[-1].content)),
    ])
    route = str(response.content).strip().lower()
    return {"route": route if route in ("tracer_data", "general") else "general"}


def chat_agent_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Chat agent with tools for Tracer data queries."""
    auth = _extract_auth(state, config)
    msgs = list(state.get("messages", []))
    if not msgs or not isinstance(msgs[0], SystemMessage):
        msgs = [SystemMessage(content=SYSTEM_PROMPT)] + msgs

    response = _get_llm().bind_tools(_get_tools()).invoke(msgs)
    return {"messages": [response], **auth}


def general_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Direct LLM response without tools."""
    auth = _extract_auth(state, config)
    msgs = list(state.get("messages", []))
    if not msgs or not isinstance(msgs[0], SystemMessage):
        msgs = [SystemMessage(content=SYSTEM_PROMPT)] + msgs

    response = _get_llm().invoke(msgs)
    return {"messages": [response], **auth}


def route_by_intent(state: AgentState) -> Literal["chat_agent", "general"]:
    """Conditional edge for chat routing."""
    return "chat_agent" if state.get("route") == "tracer_data" else "general"


def should_call_tools(state: AgentState) -> Literal["tools", "__end__"]:
    """Check if agent wants to call tools."""
    msgs = state.get("messages", [])
    if msgs and hasattr(msgs[-1], "tool_calls") and msgs[-1].tool_calls:
        return "tools"
    return "__end__"


def route_by_mode(state: AgentState) -> Literal["router", "extract_alert"]:
    """Route based on mode: chat or investigation."""
    return "extract_alert" if state.get("mode") == "investigation" else "router"


def build_graph(config: Any | None = None) -> Any:
    """Build unified agent graph.

    Routes by mode:
    - chat: router → (tracer_data → chat_agent ⟷ tools) | (general → general) → END
    - investigation: extract_alert → build_context → frame_problem → ... → END
    """
    _ = config
    graph = StateGraph(AgentState)

    # Chat nodes
    graph.add_node("router", router_node)
    graph.add_node("chat_agent", chat_agent_node)
    graph.add_node("general", general_node)
    graph.add_node("tools", ToolNode(_get_tools()))

    # Investigation nodes
    graph.add_node("extract_alert", node_extract_alert)
    graph.add_node("build_context", node_build_context)
    graph.add_node("frame_problem", node_frame_problem)
    graph.add_node("plan_actions", node_plan_actions)
    graph.add_node("investigate", node_investigate)
    graph.add_node("diagnose_root_cause", node_diagnose_root_cause)
    graph.add_node("publish_findings", node_publish_findings)

    # Entry: route by mode
    graph.add_conditional_edges(START, route_by_mode, {
        "router": "router",
        "extract_alert": "extract_alert",
    })

    # Chat flow
    graph.add_conditional_edges("router", route_by_intent, {
        "chat_agent": "chat_agent",
        "general": "general",
    })
    graph.add_conditional_edges("chat_agent", should_call_tools, {
        "tools": "tools",
        "__end__": END,
    })
    graph.add_edge("tools", "chat_agent")
    graph.add_edge("general", END)

    # Investigation flow
    graph.add_edge("build_context", "frame_problem")
    graph.add_edge("frame_problem", "plan_actions")
    graph.add_edge("plan_actions", "investigate")
    graph.add_edge("investigate", "diagnose_root_cause")
    graph.add_conditional_edges(
        "diagnose_root_cause",
        should_continue_investigation,
        {"investigate": "plan_actions", "publish_findings": "publish_findings"},
    )
    graph.add_edge("publish_findings", END)

    # Add parallel edge for investigation
    graph.add_edge("extract_alert", "build_context")

    return graph.compile()


def resolve_checkpointer_config(
    thread_id: str | None, checkpointer: Any | None
) -> tuple[Any, dict[str, Any]]:
    """Resolve checkpointer and config for graph execution."""
    _ = checkpointer
    compiled = build_graph()
    cfg = {"configurable": {"thread_id": thread_id}} if thread_id else {}
    return compiled, cfg


def run_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
    thread_id: str | None = None,
    checkpointer: Any | None = None,
) -> AgentState:
    """Run investigation graph. Pure function: inputs in, state out."""
    compiled, cfg = resolve_checkpointer_config(thread_id, checkpointer)
    initial = make_initial_state(alert_name, pipeline_name, severity, raw_alert=raw_alert)
    return cast(AgentState, compiled.invoke(initial, config=cfg))


# Pre-compiled for import
agent = build_graph()
