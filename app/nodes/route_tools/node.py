"""Route tools node - selects relevant tools for investigation planning."""

from __future__ import annotations

from langsmith import traceable

from app.nodes.plan_actions.detect_sources import detect_sources
from app.nodes.route_tools.route_tools import route_tools
from app.output import debug_print, get_tracker
from app.state import InvestigationState


@traceable(name="node_route_tools")
def node_route_tools(state: InvestigationState) -> dict:
    """Route investigation to the most relevant tools.

    This node runs before planning and selects a subset of tools based on:
    - Detected data sources from the alert
    - Resolved integration credentials
    - Keywords extracted from problem statement and alert
    - Prior execution history (deprioritizes already executed tools)

    The routed tool subset is stored in state for the planning node to consume,
    reducing the tool list sent to the LLM and improving planning speed/reliability.

    Args:
        state: InvestigationState containing alert info, context, and history

    Returns:
        Dictionary with routed_actions and available_sources for state update
    """
    tracker = get_tracker()
    tracker.start("route_tools", "Routing to relevant tools")

    # Extract required state fields
    raw_alert = state.get("raw_alert", {})
    context = state.get("context", {})
    resolved_integrations = state.get("resolved_integrations", {})
    problem_md = state.get("problem_md", "")
    alert_name = state.get("alert_name", "")
    executed_hypotheses = state.get("executed_hypotheses", [])

    # Detect available sources (same logic as plan_actions)
    available_sources = detect_sources(
        raw_alert=raw_alert,
        context=context,
        resolved_integrations=resolved_integrations,
    )

    # Enhance sources with dynamically discovered information from evidence
    evidence = state.get("evidence", {})
    s3_object = evidence.get("s3_object", {})
    if s3_object.get("found") and s3_object.get("metadata", {}).get("audit_key"):
        audit_key = s3_object["metadata"]["audit_key"]
        bucket = s3_object.get("bucket")
        if bucket and "s3_audit" not in available_sources:
            available_sources["s3_audit"] = {"bucket": bucket, "key": audit_key}
            debug_print(f"Added s3_audit source: s3://{bucket}/{audit_key}")

    debug_print(f"RouteTools: Detected sources: {list(available_sources.keys())}")

    # Route to relevant tools
    routed_tools = route_tools(
        available_sources=available_sources,
        resolved_integrations=resolved_integrations,
        problem_md=problem_md,
        alert_name=alert_name,
        executed_hypotheses=executed_hypotheses,
    )

    routed_action_names = [tool.name for tool in routed_tools]

    debug_print(f"RouteTools: Selected {len(routed_action_names)} tools: {routed_action_names}")

    tracker.complete(
        "route_tools",
        fields_updated=["routed_actions", "available_sources"],
        message=f"Routed to {len(routed_action_names)} tools from {list(available_sources.keys())} sources",
    )

    return {
        "routed_actions": routed_action_names,
        "available_sources": available_sources,
    }
