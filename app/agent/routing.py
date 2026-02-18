"""Graph routing logic - conditional edges and flow control."""

from __future__ import annotations

from app.agent.output import debug_print
from app.agent.state import AgentState, InvestigationState


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

    This function implements the conditional routing logic after validation:
    - If confidence/validity is too low AND there are recommendations, loop back
    - If max loops reached, proceed to publish findings
    - If no actions can be planned, stop looping (safety check)
    - Otherwise, proceed to publish findings

    Args:
        state: Current investigation state

    Returns:
        Next node name: "investigate" or "publish"
    """
    try:
        confidence = state.get("confidence", 0.0)
        validity_score = state.get("validity_score", 0.0)
        investigation_recommendations = state.get("investigation_recommendations", [])
        loop_count = state.get("investigation_loop_count", 0)
        available_action_names = state.get("available_action_names", [])
        max_loops = 4  # Maximum 4 additional loops (5 total loops max)

        print(
            f"[DEBUG] Routing: confidence={confidence:.0%}, validity={validity_score:.0%}, "
            f"loop={loop_count}/{max_loops}, recommendations={len(investigation_recommendations)}, "
            f"available_actions={len(available_action_names)}"
        )

        # Safety check: if no actions are available, we can't gather more evidence
        if not available_action_names:
            debug_print("No available actions -> publish (safety check)")
            return "publish"

        # Check loop limit first
        if loop_count > max_loops:
            debug_print(f"Max loops ({max_loops}) exceeded -> publish")
            return "publish"

        # Continue investigation if:
        # 1. confidence or validity is low AND we have recommendations, OR
        # 2. we have recommendations (regardless of confidence) for critical missing evidence
        confidence_threshold = 0.6
        validity_threshold = 0.5

        low_confidence_or_validity = (
            confidence < confidence_threshold or validity_score < validity_threshold
        )
        has_recommendations = bool(investigation_recommendations)

        # Loop back if low confidence/validity OR if recommendations exist (critical evidence missing)
        should_loop = (low_confidence_or_validity and has_recommendations) or (
            has_recommendations and loop_count <= max_loops
        )

        print(
            f"[DEBUG] Routing decision: should_loop={should_loop}, "
            f"low_conf_or_val={low_confidence_or_validity}, has_recs={has_recommendations}, "
            f"loop={loop_count}/{max_loops}"
        )

        if should_loop:
            print("[DEBUG] Routing -> investigate (looping back)")
            return "investigate"

        print("[DEBUG] Routing -> publish")
        return "publish"
    except Exception as e:
        # If there's any error, log it and default to publishing findings
        import sys

        print(f"[ERROR] Routing function failed: {e}", file=sys.stderr)
        return "publish"
