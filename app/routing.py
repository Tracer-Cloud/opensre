"""Graph routing logic - conditional edges and flow control."""

from __future__ import annotations

from app.output import debug_print
from app.state import AgentState, InvestigationState


def route_by_mode(state: AgentState) -> str:
    """Route based on agent mode. Defaults to chat when mode is not set."""
    return "investigation" if state.get("mode") == "investigation" else "chat"


def route_chat(state: AgentState) -> str:
    """Route chat messages by intent."""
    return "tracer_data" if state.get("route") == "tracer_data" else "general"


def route_after_extract(state: AgentState) -> str:
    """Route after alert extraction - skip investigation if noise."""
    return "end" if state.get("is_noise") else "investigate"


def route_investigation_loop(state: AgentState) -> str:
    """Decide whether to continue investigation loop."""
    return should_continue_investigation(state)


def should_call_tools(state: AgentState) -> str:
    """Check if the last AI message has tool calls that need execution."""
    msgs = list(state.get("messages", []))
    if msgs:
        last = msgs[-1]
        if hasattr(last, "tool_calls") and getattr(last, "tool_calls", None):
            return "call_tools"
    return "done"


def should_continue_investigation(state: InvestigationState) -> str:
    """
    Decide whether to continue investigation or publish findings.

    Loops back to investigate while recommendations exist and loop limit is not reached.
    Publishes findings when recommendations are exhausted, max loops exceeded, or no
    actions are available.
    """
    try:
        investigation_recommendations = state.get("investigation_recommendations", [])
        loop_count = state.get("investigation_loop_count", 0)
        available_action_names = state.get("available_action_names", [])
        max_loops = 4  # Maximum 4 additional loops (5 total loops max)

        # Safety check: if no actions are available, we can't gather more evidence
        if not available_action_names:
            debug_print("No available actions -> publish (safety check)")
            return "publish"

        # Check loop limit first
        if loop_count > max_loops:
            debug_print(f"Max loops ({max_loops}) exceeded -> publish")
            return "publish"

        # Loop while there are recommendations for additional evidence to gather
        if investigation_recommendations:
            debug_print(f"Has recommendations -> investigate (loop {loop_count}/{max_loops})")
            return "investigate"

        return "publish"
    except Exception as e:
        debug_print(f"Routing function failed: {e} -> publish")
        return "publish"
