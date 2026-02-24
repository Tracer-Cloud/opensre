"""Unified agent pipeline - wires nodes and edges into a LangGraph."""

from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.nodes import (
    node_build_context,
    node_diagnose_root_cause,
    node_extract_alert,
    node_plan_actions,
    node_publish_findings,
    node_resolve_integrations,
)
from app.agent.nodes.auth import inject_auth_node
from app.agent.nodes.chat import (
    chat_agent_node,
    general_node,
    router_node,
    tool_executor_node,
)
from app.agent.nodes.investigate.node import node_investigate
from app.agent.routing import (
    route_after_extract,
    route_by_mode,
    route_chat,
    route_investigation_loop,
    should_call_tools,
)
from app.agent.runners import SimpleAgent
from app.agent.state import AgentState


def build_graph(config: None = None) -> CompiledStateGraph:
    """Build and compile the LangGraph agent."""
    _ = config
    graph = StateGraph(AgentState)

    graph.add_node("inject_auth", inject_auth_node)

    graph.add_node("router", router_node)
    graph.add_node("chat_agent", chat_agent_node)  # type: ignore[arg-type]
    graph.add_node("general", general_node)  # type: ignore[arg-type]
    graph.add_node("tool_executor", tool_executor_node)

    graph.add_node("extract_alert", node_extract_alert)
    graph.add_node("resolve_integrations", node_resolve_integrations)
    graph.add_node("build_context", node_build_context)
    graph.add_node("plan_actions", node_plan_actions)
    graph.add_node("investigate", node_investigate)
    graph.add_node("diagnose", node_diagnose_root_cause)
    graph.add_node("publish", node_publish_findings)

    graph.set_entry_point("inject_auth")

    graph.add_conditional_edges("inject_auth", route_by_mode, {"chat": "router", "investigation": "extract_alert"})

    graph.add_conditional_edges("router", route_chat, {"tracer_data": "chat_agent", "general": "general"})
    graph.add_conditional_edges("chat_agent", should_call_tools, {"call_tools": "tool_executor", "done": END})
    graph.add_edge("tool_executor", "chat_agent")
    graph.add_edge("general", END)

    graph.add_conditional_edges("extract_alert", route_after_extract, {"end": END, "investigate": "resolve_integrations"})
    graph.add_edge("resolve_integrations", "build_context")
    graph.add_edge("build_context", "plan_actions")
    graph.add_edge("plan_actions", "investigate")
    graph.add_edge("investigate", "diagnose")
    graph.add_conditional_edges("diagnose", route_investigation_loop, {"investigate": "plan_actions", "publish": "publish"})
    graph.add_edge("publish", END)

    return graph.compile()


agent = SimpleAgent()
graph = build_graph()
