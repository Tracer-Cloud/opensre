"""Investigation Graph - Orchestrates the incident resolution workflow."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    node_diagnose_root_cause,
    node_frame_problem,
    node_generate_hypotheses,
    node_hypothesis_investigation,
    node_publish_findings,
)
from src.agent.state import InvestigationState, make_initial_state


def build_graph_pipeline() -> StateGraph:
    """
    Build the investigation state machine.

    Linear flow:
        START
        → frame_problem          # Enrich incident context
        → generate_hypotheses    # Determine what to investigate
        → hypothesis_investigation  # Build context and gather evidence
        → diagnose_root_cause    # Synthesize conclusion
        → publish_findings       # Format outputs
        → END
    """
    graph = StateGraph(InvestigationState)

    # Nodes define the agentic steps in the graph pipeline
    graph.add_node("frame_problem", node_frame_problem)
    graph.add_node("generate_hypotheses", node_generate_hypotheses)
    graph.add_node("hypothesis_investigation", node_hypothesis_investigation)
    graph.add_node("diagnose_root_cause", node_diagnose_root_cause)
    graph.add_node("publish_findings_to_slack", node_publish_findings)

    # Edges define the shape of the graph pipeline
    graph.add_edge(START, "frame_problem")
    graph.add_edge("frame_problem", "generate_hypotheses")
    graph.add_edge("generate_hypotheses", "hypothesis_investigation")
    graph.add_edge("hypothesis_investigation", "diagnose_root_cause")
    graph.add_edge("diagnose_root_cause", "publish_findings_to_slack")
    graph.add_edge("publish_findings_to_slack", END)

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

