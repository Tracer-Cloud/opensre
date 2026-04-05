"""Auth injection node - extracts auth context from LangGraph config."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.state import AgentState


def _extract_auth(state: AgentState, config: RunnableConfig) -> dict[str, str]:
    """Extract auth context and LangGraph metadata from config."""
    configurable = config.get("configurable", {})
    auth = configurable.get("langgraph_auth_user", {})

    thread_id = configurable.get("thread_id", "") or state.get("thread_id", "")
    run_id = configurable.get("run_id", "") or state.get("run_id", "")

    return {
        "org_id": auth.get("org_id") or state.get("org_id", ""),
        "user_id": auth.get("identity") or state.get("user_id", ""),
        "user_email": auth.get("email", ""),
        "user_name": auth.get("full_name", ""),
        "organization_slug": auth.get("organization_slug", ""),
        "thread_id": thread_id,
        "run_id": run_id,
    }


def inject_auth_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Extract auth context from JWT and inject into state."""
    return _extract_auth(state, config)
