"""Standalone runners for testing and CLI - run the graph without LangGraph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from app.agent.nodes import (
    node_build_context,
    node_diagnose_root_cause,
    node_extract_alert,
    node_plan_actions,
    node_publish_findings,
    node_resolve_integrations,
)
from app.agent.nodes.chat import chat_agent_node, general_node, router_node
from app.agent.nodes.investigate.node import node_investigate
from app.agent.routing import should_continue_investigation
from app.agent.state import AgentState, make_initial_state


def _merge_state(state: AgentState, updates: dict[str, Any]) -> None:
    if not updates:
        return
    state_any = cast(dict[str, Any], state)
    for key, value in updates.items():
        if key == "messages":
            messages = list(state_any.get("messages", []))
            messages.extend(value) if isinstance(value, list) else messages.append(value)
            state_any["messages"] = messages
            continue
        state_any[key] = value


def run_chat(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
    """Run chat routing + response without LangGraph (for testing)."""
    cfg = config or {"configurable": {}}
    _merge_state(state, router_node(state))
    if state.get("route") == "tracer_data":
        _merge_state(state, chat_agent_node(state, cfg))
    else:
        _merge_state(state, general_node(state, cfg))
    return state


def _run_investigation_pipeline(state: AgentState) -> AgentState:
    """Run investigation pipeline sequentially without LangGraph."""
    _merge_state(state, node_extract_alert(state))

    if state.get("is_noise"):
        return state

    _merge_state(state, node_resolve_integrations(state))
    _merge_state(state, node_build_context(state))

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
        return run_chat(state, config) if state.get("mode") == "chat" else _run_investigation_pipeline(state)
