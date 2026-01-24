"""Investigation Graph - Orchestrates the incident resolution workflow."""

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    node_collect_evidence,
    node_diagnose_root_cause,
    node_frame_problem,
    node_generate_hypotheses,
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
        → collect_evidence       # Gather supporting data
        → diagnose_root_cause    # Synthesize conclusion
        → publish_findings       # Format outputs
        → END
    """
    graph = StateGraph(InvestigationState)

    graph.add_node("frame_problem", node_frame_problem)
    graph.add_node("generate_hypotheses", node_generate_hypotheses)
    graph.add_node("collect_evidence", node_collect_evidence)
    graph.add_node("diagnose_root_cause", node_diagnose_root_cause)
    graph.add_node("publish_findings", node_publish_findings)

    graph.add_edge(START, "frame_problem")
    graph.add_edge("frame_problem", "generate_hypotheses")
    graph.add_edge("generate_hypotheses", "collect_evidence")
    graph.add_edge("collect_evidence", "diagnose_root_cause")
    graph.add_edge("diagnose_root_cause", "publish_findings")
    graph.add_edge("publish_findings", END)

    return graph.compile()


def build_graph() -> StateGraph:
    """Backwards-compatible alias for the compiled graph."""
    return build_graph_pipeline()

def run_investigation_pipeline(alert_name: str, affected_table: str, severity: str) -> InvestigationState:
    """
    Run the investigation graph.

    Pure function: inputs in, state out. No rendering.
    """
    graph = build_graph_pipeline()

    initial_state = make_initial_state(alert_name, affected_table, severity)

    # Run the graph
    final_state = graph.invoke(initial_state)

    return final_state

