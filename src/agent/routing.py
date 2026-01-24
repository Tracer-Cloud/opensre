"""Graph routing logic - conditional edges and flow control."""

from src.agent.state import InvestigationState


def should_continue_investigation(state: InvestigationState) -> str:
    """
    Decide whether to continue investigation or publish findings.

    This function implements the conditional routing logic after validation:
    - If confidence/validity is too low AND there are recommendations, loop back
    - If max loops reached, proceed to publish findings
    - Otherwise, proceed to publish findings

    Args:
        state: Current investigation state

    Returns:
        Next node name: "investigate" or "publish_findings"
    """
    # CRITICAL: Print to stderr first - this will always show if function is called
    import sys

    print("\n[ROUTING] should_continue_investigation() called", file=sys.stderr, flush=True)

    try:
        from src.agent.nodes.publish_findings.render import console

        confidence = state.get("confidence", 0.0)
        validity_score = state.get("validity_score", 0.0)
        investigation_recommendations = state.get("investigation_recommendations", [])
        loop_count = state.get("investigation_loop_count", 0)
        max_loops = 1  # Maximum 1 additional loop (2 total loops max)

        # Debug logging - this MUST execute to see what's happening
        console.print("\n  [bold yellow]🔀 ROUTING DECISION[/]")
        console.print(
            f"  [dim]  confidence={confidence:.0%}, validity={validity_score:.0%}, loop={loop_count}/{max_loops}, recommendations={len(investigation_recommendations)}[/]"
        )

        # Check loop limit first
        if loop_count >= max_loops:
            console.print(
                f"\n  [yellow]⚠️  Maximum investigation loops ({max_loops}) reached. Proceeding to publish findings.[/]"
            )
            return "publish_findings"

        # Continue investigation if confidence or validity is low AND we have recommendations
        confidence_threshold = 0.6
        validity_threshold = 0.5

        should_loop = (
            confidence < confidence_threshold or validity_score < validity_threshold
        ) and bool(investigation_recommendations)

        if should_loop:
            console.print(
                "  [cyan]→ Looping back to investigation (low confidence/validity with recommendations)[/]"
            )
            return "investigate"

        console.print(
            "  [green]→ Proceeding to publish findings (thresholds met or no recommendations)[/]"
        )
        return "publish_findings"
    except Exception as e:
        # If there's any error, log it and default to publishing findings
        import sys

        print(f"\n  [ERROR] Routing function failed: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        # Default to publishing findings on error
        return "publish_findings"
