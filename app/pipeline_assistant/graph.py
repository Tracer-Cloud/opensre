"""Pipeline assistant graph for debugging conversations.

A simple chat agent that helps users with pipeline-related questions.
Authentication is handled via JWT tokens with org_id scoping.
"""

import os
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END

from app.pipeline_assistant.state import PipelineAssistantState

SYSTEM_PROMPT = """You are a pipeline debugging assistant for Tracer.

Your job is to help users understand and debug their bioinformatics pipelines.

Guidelines:
- Ask clarifying questions to understand the user's issue
- Provide clear, actionable advice
- If you need more context about their pipeline, ask for it"""


def get_llm() -> ChatAnthropic:
    """Get LLM instance."""
    return ChatAnthropic(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        max_tokens=4096,
    )


def agent_node(
    state: PipelineAssistantState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Main agent node - calls LLM.

    Extracts user info from authenticated context.
    """
    auth_user = config.get("configurable", {}).get("langgraph_auth_user", {})
    org_id = auth_user.get("org_id") or state.get("org_id", "")
    user_id = auth_user.get("identity") or state.get("user_id", "")

    messages = list(state.get("messages", []))

    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages

    response = get_llm().invoke(messages)

    result: dict[str, Any] = {"messages": [response]}
    if auth_user:
        result["org_id"] = org_id
        result["user_id"] = user_id
        result["user_email"] = auth_user.get("email", "")
        result["user_name"] = auth_user.get("full_name", "")
        result["organization_slug"] = auth_user.get("organization_slug", "")

    return result


def build_graph() -> StateGraph:
    """Build the pipeline assistant graph."""
    graph = StateGraph(PipelineAssistantState)
    graph.add_node("agent", agent_node)
    graph.set_entry_point("agent")
    graph.add_edge("agent", END)
    return graph.compile()


pipeline_assistant = build_graph()
