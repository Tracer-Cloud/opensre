"""Pipeline assistant graph for debugging conversations.

A chat agent that helps users with pipeline-related questions.
Authentication is handled via JWT tokens with org_id scoping.

The graph routes messages based on intent:
- Tracer data queries → agent with tools (pipelines, runs, logs, metrics)
- General questions → direct LLM response
"""

import os
from typing import Any, Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.pipeline_assistant.config import DEFAULT_MAX_TOKENS, DEFAULT_MODEL
from app.pipeline_assistant.prompts import (
    PIPELINE_ASSISTANT_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
)
from app.pipeline_assistant.state import PipelineAssistantState
from app.pipeline_assistant.tools import get_pipeline_assistant_tools


def get_llm() -> ChatAnthropic:
    """Get LLM instance.

    Uses the model specified in ANTHROPIC_MODEL env var,
    falling back to the default model from config.
    """
    return ChatAnthropic(  # type: ignore[call-arg]
        model=os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL),
        max_tokens=DEFAULT_MAX_TOKENS,
    )


def get_llm_with_tools() -> Any:
    """Get LLM instance with tools bound for Tracer data queries."""
    tools = get_pipeline_assistant_tools()
    return get_llm().bind_tools(tools)


def _extract_auth_context(
    state: PipelineAssistantState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Extract authentication context from config and state."""
    auth_user = config.get("configurable", {}).get("langgraph_auth_user", {})
    return {
        "org_id": auth_user.get("org_id") or state.get("org_id", ""),
        "user_id": auth_user.get("identity") or state.get("user_id", ""),
        "user_email": auth_user.get("email", ""),
        "user_name": auth_user.get("full_name", ""),
        "organization_slug": auth_user.get("organization_slug", ""),
    }


def router_node(
    state: PipelineAssistantState,
    config: RunnableConfig,  # noqa: ARG001
) -> dict[str, Any]:
    """Route user messages based on intent.

    Classifies whether the user is asking for Tracer data or a general question.

    Args:
        state: Current conversation state.
        config: Runtime configuration with auth context (unused but required by LangGraph).

    Returns:
        Updated state with route decision.
    """
    messages = list(state.get("messages", []))
    if not messages:
        return {"route": "general"}

    # Get the last user message
    last_message = messages[-1]
    if not isinstance(last_message, HumanMessage):
        return {"route": "general"}

    # Classify intent using LLM
    router_messages = [
        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=str(last_message.content)),
    ]

    response = get_llm().invoke(router_messages)
    route = str(response.content).strip().lower()

    # Default to general if classification is unclear
    if route not in ("tracer_data", "general"):
        route = "general"

    return {"route": route}


def agent_node(
    state: PipelineAssistantState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Main agent node - processes user messages with tools for Tracer data.

    This node is used when the user is asking for data from Tracer.
    It has access to tools for querying pipelines, runs, logs, etc.

    Args:
        state: Current conversation state.
        config: Runtime configuration with auth context.

    Returns:
        Updated state with new message and user info.
    """
    auth_context = _extract_auth_context(state, config)

    # Build message list with system prompt
    messages = list(state.get("messages", []))
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=PIPELINE_ASSISTANT_SYSTEM_PROMPT)] + messages

    # Generate response with tools
    llm = get_llm_with_tools()
    response = llm.invoke(messages)

    # Build result
    result: dict[str, Any] = {"messages": [response]}
    result.update(auth_context)

    return result


def general_node(
    state: PipelineAssistantState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """General response node - answers questions without Tracer data.

    This node is used for general questions that don't require
    querying the Tracer system.

    Args:
        state: Current conversation state.
        config: Runtime configuration with auth context.

    Returns:
        Updated state with new message and user info.
    """
    auth_context = _extract_auth_context(state, config)

    # Build message list with system prompt
    messages = list(state.get("messages", []))
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=PIPELINE_ASSISTANT_SYSTEM_PROMPT)] + messages

    # Generate response without tools
    response = get_llm().invoke(messages)

    # Build result
    result: dict[str, Any] = {"messages": [response]}
    result.update(auth_context)

    return result


def route_by_intent(state: PipelineAssistantState) -> Literal["agent", "general"]:
    """Conditional edge function to route based on classified intent."""
    route = state.get("route", "general")
    if route == "tracer_data":
        return "agent"
    return "general"


def should_continue(state: PipelineAssistantState) -> Literal["tools", "__end__"]:
    """Check if the agent wants to call tools."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last_message = messages[-1]
    # Check if the last message has tool calls
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return "__end__"


def build_graph() -> Any:
    """Build the pipeline assistant graph with routing.

    Graph flow:
        START → router → (tracer_data) → agent ⟷ tools → END
                       → (general) → general → END

    Returns:
        Compiled StateGraph ready for execution.
    """
    tools = get_pipeline_assistant_tools()
    tool_node = ToolNode(tools)

    graph = StateGraph(PipelineAssistantState)

    # Add nodes
    graph.add_node("router", router_node)
    graph.add_node("agent", agent_node)
    graph.add_node("general", general_node)
    graph.add_node("tools", tool_node)

    # Set entry point
    graph.set_entry_point("router")

    # Add conditional routing from router
    graph.add_conditional_edges(
        "router",
        route_by_intent,
        {
            "agent": "agent",
            "general": "general",
        },
    )

    # Agent can call tools or finish
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "__end__": END,
        },
    )

    # Tools return to agent for further processing
    graph.add_edge("tools", "agent")

    # General node goes directly to end
    graph.add_edge("general", END)

    return graph.compile()


# Pre-compiled graph instance for import
pipeline_assistant = build_graph()
