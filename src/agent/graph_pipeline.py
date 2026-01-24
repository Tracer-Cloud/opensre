"""Investigation Graph - Orchestrates the incident resolution workflow."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    node_diagnose_root_cause,
    node_frame_problem,
    node_publish_findings,
)
from src.agent.nodes.investigate.investigate import node_investigate
from src.agent.state import InvestigationState, make_initial_state


def build_graph_pipeline() -> StateGraph:
    """
    Build the investigation state machine.

    Simplified flow:
        START
        → frame_problem    # Enrich incident context + get tracer run metadata
        → investigate      # Determine tools and gather evidence in one go
        → diagnose_root_cause    # Synthesize conclusion with validation
        → publish_findings       # Format outputs
        → END
    """
    graph = StateGraph(InvestigationState)

    # Nodes define the agentic steps in the graph pipeline
    graph.add_node("frame_problem", node_frame_problem)
    graph.add_node("investigate", node_investigate)
    graph.add_node("diagnose_root_cause", node_diagnose_root_cause)
    graph.add_node("publish_findings", node_publish_findings)

    # Edges define the shape of the graph pipeline
    graph.add_edge(START, "frame_problem")
    graph.add_edge("frame_problem", "investigate")
    graph.add_edge("investigate", "diagnose_root_cause")

    # Conditional edge: if confidence/validity is too low, loop back to investigate
    from src.agent.routing import should_continue_investigation

    graph.add_conditional_edges(
        "diagnose_root_cause",
        should_continue_investigation,
        {
            "investigate": "investigate",
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
