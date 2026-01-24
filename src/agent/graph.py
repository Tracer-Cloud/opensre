"""Investigation Graph - Orchestrates the incident resolution workflow."""

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    node_collect_evidence,
    node_diagnose_root_cause,
    node_generate_hypotheses,
    node_generate_reports,
)
from src.agent.state import InvestigationState, make_initial_state


def build_graph() -> StateGraph:
    """Build the investigation state machine."""
    graph = StateGraph(InvestigationState)

    # Problem framing node should be added here frame_problem.py
    graph.add_node("hypotheses", node_generate_hypotheses)
    graph.add_node("evidence", node_collect_evidence)
    graph.add_node("diagnose", node_diagnose_root_cause)
    graph.add_node("reports", node_generate_reports)

    graph.add_edge(START, "hypotheses")
    graph.add_edge("hypotheses", "evidence")
    graph.add_edge("evidence", "diagnose")
    graph.add_edge("diagnose", "reports")
    graph.add_edge("reports", END)

    return graph.compile()


def run_investigation(alert_name: str, affected_table: str, severity: str) -> InvestigationState:
    """
    Run the investigation graph.

    Pure function: inputs in, state out. No rendering.
    """
    graph = build_graph()

    initial_state = make_initial_state(alert_name, affected_table, severity)

    # Run the graph
    final_state = graph.invoke(initial_state)

    return final_state

