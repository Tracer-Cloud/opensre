"""Standalone runners for testing and CLI — run the pipeline without LangGraph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from app.nodes.chat import chat_agent_node, general_node, router_node
from app.state import AgentState, make_initial_state


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


def run_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
    resolved_integrations: dict[str, Any] | None = None,
) -> AgentState:
    """Run investigation pipeline via LangGraph. Pure function: inputs in, state out.

    Args:
        resolved_integrations: Optional pre-resolved integrations dict. When provided,
            node_resolve_integrations is skipped — useful for synthetic testing where a
            FixtureGrafanaBackend should be injected without real credential resolution.
    """
    from app.pipeline.graph import graph as compiled_graph  # lazy to avoid circular import

    initial = make_initial_state(alert_name, pipeline_name, severity, raw_alert=raw_alert)
    if resolved_integrations is not None:
        cast(dict[str, Any], initial)["resolved_integrations"] = resolved_integrations
    return cast(AgentState, compiled_graph.invoke(initial))


@dataclass
class SimpleAgent:
    def invoke(
        self, state: AgentState, config: RunnableConfig | None = None
    ) -> AgentState:
        from app.pipeline.graph import graph as compiled_graph  # lazy to avoid circular import

        cfg = config or {"configurable": {}}
        return cast(AgentState, compiled_graph.invoke(state, cfg))
