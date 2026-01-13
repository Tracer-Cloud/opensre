"""
Investigation Graph - Thin orchestrator for the LangGraph state machine.

The graph is explicit:
    START → check_s3 → check_nextflow → determine_root_cause → output → END

Layered architecture:
    - infrastructure/: External clients (S3, Nextflow) and LLM
    - domain/: State, prompts, and pure tools
    - presentation/: UI rendering and report formatting
    - nodes.py: Node orchestration
    - graph.py: Graph definition (this file)
"""

from langgraph.graph import StateGraph, START, END

# Domain layer
from src.agent.domain.state import InvestigationState

# Nodes (orchestration)
from src.agent.nodes import (
    node_check_s3,
    node_check_nextflow,
    node_determine_root_cause,
    node_output,
)

# Presentation layer
from src.agent.presentation.render import render_investigation_start, render_agent_output


# ─────────────────────────────────────────────────────────────────────────────
# BUILD THE GRAPH
# ─────────────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """Build the investigation state machine."""
    graph = StateGraph(InvestigationState)

    # Add nodes
    graph.add_node("check_s3", node_check_s3)
    graph.add_node("check_nextflow", node_check_nextflow)
    graph.add_node("determine_root_cause", node_determine_root_cause)
    graph.add_node("output", node_output)

    # Add edges (linear flow)
    graph.add_edge(START, "check_s3")
    graph.add_edge("check_s3", "check_nextflow")
    graph.add_edge("check_nextflow", "determine_root_cause")
    graph.add_edge("determine_root_cause", "output")
    graph.add_edge("output", END)

    return graph.compile()


def run_investigation(alert_name: str, affected_table: str, severity: str) -> InvestigationState:
    """Run the investigation graph."""
    render_investigation_start(alert_name, affected_table, severity)

    graph = build_graph()

    initial_state: InvestigationState = {
        "alert_name": alert_name,
        "affected_table": affected_table,
        "severity": severity,
        "s3_marker_exists": None,
        "s3_file_count": 0,
        "nextflow_finalize_status": None,
        "nextflow_logs": None,
        "root_cause": None,
        "confidence": 0.0,
        "slack_message": None,
        "problem_md": None,
    }

    # Run the graph
    final_state = graph.invoke(initial_state)

    # Print outputs
    render_agent_output(final_state["slack_message"])

    return final_state

