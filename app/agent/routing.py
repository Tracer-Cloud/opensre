"""Graph routing logic - conditional edges and flow control."""

from app.agent.output import debug_print
from app.agent.state import InvestigationState


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
        Next node name: "investigate" or "publish_findings"
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
            debug_print("No available actions -> publish_findings (safety check)")
            return "publish_findings"

        # Check loop limit first
        if loop_count > max_loops:
            debug_print(f"Max loops ({max_loops}) exceeded -> publish_findings")
            return "publish_findings"

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

        print("[DEBUG] Routing -> publish_findings")
        return "publish_findings"
    except Exception as e:
        # If there's any error, log it and default to publishing findings
        import sys

        print(f"[ERROR] Routing function failed: {e}", file=sys.stderr)
        return "publish_findings"
