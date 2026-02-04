"""Unified agent pipeline - handles both chat and investigation modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

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

SYSTEM_PROMPT = """You are a pipeline debugging assistant for Tracer.
You help users understand and debug their bioinformatics pipelines.

You have access to tools for querying pipeline data, runs, logs, and metrics.
Use these tools when users ask about their pipelines, failed runs, or need debugging help.

For general questions about bioinformatics or pipeline best practices, answer directly."""

ROUTER_PROMPT = """Classify the user message:
- "tracer_data" if asking about pipelines, runs, logs, metrics, failures, or debugging
- "general" for general questions, greetings, or best practices

Respond with ONLY: tracer_data or general"""


def _merge_state(state: AgentState, updates: dict[str, Any]) -> None:
    if not updates:
        return
    for key, value in updates.items():
        if key == "messages":
            messages = list(state.get("messages", []))
            if isinstance(value, list):
                messages.extend(value)
            else:
                messages.append(value)
            state["messages"] = messages
            continue
        state[key] = value


def _extract_auth(state: AgentState, config: dict[str, Any] | None) -> dict[str, str]:
    """Extract auth context from config."""
    cfg = config or {}
    auth = cfg.get("configurable", {}).get("langgraph_auth_user", {})
    return {
        "org_id": auth.get("org_id") or state.get("org_id", ""),
        "user_id": auth.get("identity") or state.get("user_id", ""),
        "user_email": auth.get("email", ""),
        "user_name": auth.get("full_name", ""),
        "organization_slug": auth.get("organization_slug", ""),
    }


# Chat mode nodes
def router_node(state: AgentState) -> dict[str, Any]:
    """Route chat messages by intent."""
    msgs = list(state.get("messages", []))
    if not msgs or msgs[-1].get("role") != "user":
        return {"route": "general"}

    response = get_llm().invoke([
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": str(msgs[-1].get("content", ""))},
    ])
    route = str(response.content).strip().lower()
    return {"route": route if route in ("tracer_data", "general") else "general"}


def _respond_with_llm(
    state: AgentState,
    config: dict[str, Any] | None,
    *,
    bind_tools: bool,
) -> dict[str, Any]:
    auth = _extract_auth(state, config)
    msgs = list(state.get("messages", []))
    if not msgs or msgs[0].get("role") != "system":
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + msgs

    llm = get_llm()
    if bind_tools:
        llm = llm.bind_tools([])
    response = llm.invoke(msgs)
    assistant_msg: ChatMessage = {"role": "assistant", "content": response.content}
    return {"messages": [assistant_msg], **auth}


def chat_agent_node(state: AgentState, config: dict[str, Any] | None) -> dict[str, Any]:
    """Chat agent with tools for Tracer data queries."""
    return _respond_with_llm(state, config, bind_tools=True)


def general_node(state: AgentState, config: dict[str, Any] | None) -> dict[str, Any]:
    """Direct LLM response without tools."""
    return _respond_with_llm(state, config, bind_tools=False)


def run_chat(state: AgentState, config: dict[str, Any] | None = None) -> AgentState:
    """Run chat routing + response without LangGraph."""
    _merge_state(state, router_node(state))
    route = state.get("route", "general")
    if route == "tracer_data":
        _merge_state(state, chat_agent_node(state, config))
    else:
        _merge_state(state, general_node(state, config))
    return state


def _run_investigation_pipeline(state: AgentState) -> AgentState:
    """Run investigation pipeline sequentially without LangGraph."""
    _merge_state(state, node_extract_alert(state))
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
    def invoke(self, state: AgentState, config: dict[str, Any] | None = None) -> AgentState:
        mode = state.get("mode", "investigation")
        if mode == "chat":
            return run_chat(state, config)
        return _run_investigation_pipeline(state)


def build_graph(config: Any | None = None) -> SimpleAgent:
    _ = config
    return agent


# Pre-compiled for import
agent = SimpleAgent()
