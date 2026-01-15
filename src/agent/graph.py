"""
Investigation Graph - Thin orchestrator for the LangGraph state machine.

The graph is explicit:
    START -> propose_hypotheses -> check_s3 -> check_tracer -> determine_root_cause -> output -> END

Layered architecture:
    - infrastructure/: External clients (S3, Tracer) and LLM
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
    node_propose_hypotheses,
    node_check_s3,
    node_check_tracer,
    node_determine_root_cause,
    node_output,
)

# Presentation layer
from src.agent.presentation.render import render_investigation_start


# ─────────────────────────────────────────────────────────────────────────────
# BUILD THE GRAPH
# ─────────────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """Build the investigation state machine."""
    graph = StateGraph(InvestigationState)

    # Add nodes
    graph.add_node("propose_hypotheses", node_propose_hypotheses)
    graph.add_node("check_s3", node_check_s3)
    graph.add_node("check_tracer", node_check_tracer)
    graph.add_node("determine_root_cause", node_determine_root_cause)
    graph.add_node("output", node_output)

    # Add edges (linear flow)
    # 1. Propose hypotheses first
    graph.add_edge(START, "propose_hypotheses")
    # 2. Execute hypothesis tests
    graph.add_edge("propose_hypotheses", "check_s3")
    graph.add_edge("check_s3", "check_tracer")
    # 3. Analyze results and output
    graph.add_edge("check_tracer", "determine_root_cause")
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
        "hypotheses": [],
        "s3_marker_exists": False,
        "s3_file_count": 0,
        "tracer_run_found": False,
        "tracer_run_id": None,
        "tracer_pipeline_name": None,
        "tracer_run_status": None,
        "tracer_run_time_seconds": 0,
        "tracer_total_tasks": 0,
        "tracer_failed_tasks": 0,
        "tracer_failed_task_details": [],
        "root_cause": "",
        "confidence": 0.0,
        "slack_message": "",
        "problem_md": "",
    }

    # Run the graph
    final_state = graph.invoke(initial_state)

    return final_state

