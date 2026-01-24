"""Investigation Graph - Orchestrates the incident resolution workflow."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    node_diagnose_root_cause,
    node_frame_problem,
    node_generate_hypotheses,
    node_hypothesis_investigation,
    node_publish_findings,
    node_validate_analysis,
)
from src.agent.state import InvestigationState, make_initial_state


def build_graph_pipeline() -> StateGraph:
    """
    Build the investigation state machine.

    Linear flow:
        START
        → frame_problem          # Enrich incident context
        → generate_hypotheses    # Determine what to investigate
        → hypothesis_execution  # Build context and gather evidence
        → diagnose_root_cause    # Synthesize conclusion
        → validate_analysis     # Check for hallucinations and unrealistic values
        → publish_findings       # Format outputs
        → END
    """
    graph = StateGraph(InvestigationState)

    # Nodes define the agentic steps in the graph pipeline
    graph.add_node("frame_problem", node_frame_problem)
    graph.add_node("generate_hypotheses", node_generate_hypotheses)
    graph.add_node("hypothesis_execution", node_hypothesis_investigation)
    graph.add_node("diagnose_root_cause", node_diagnose_root_cause)
    graph.add_node("validate_analysis", node_validate_analysis)
    graph.add_node("publish_findings", node_publish_findings)

    # Edges define the shape of the graph pipeline
    graph.add_edge(START, "frame_problem")
    graph.add_edge("frame_problem", "generate_hypotheses")
    graph.add_edge("generate_hypotheses", "hypothesis_execution")
    graph.add_edge("hypothesis_execution", "diagnose_root_cause")
    graph.add_edge("diagnose_root_cause", "validate_analysis")

    # Conditional edge: if confidence/validity is too low, loop back to generate_hypotheses
    from src.agent.graph_routing import should_continue_investigation

    graph.add_conditional_edges(
        "validate_analysis",
        should_continue_investigation,
        {
            "generate_hypotheses": "generate_hypotheses",
            "publish_findings": "publish_findings",
        },
    )

    graph.add_edge("publish_findings", END)

    return graph.compile()

def run_investigation_pipeline(
    alert_name: str,
    affected_table: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
) -> InvestigationState:
    """
    Run the investigation graph.

    Pure function: inputs in, state out. No rendering.
    """
    graph = build_graph_pipeline()

    initial_state = make_initial_state(
        alert_name,
        affected_table,
        severity,
        raw_alert=raw_alert,
    )

    # Run the graph
    final_state = graph.invoke(initial_state)

    return final_state

