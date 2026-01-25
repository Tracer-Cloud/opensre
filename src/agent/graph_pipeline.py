"""Investigation Graph - Orchestrates the incident resolution workflow."""

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.graph import CompiledStateGraph

from src.agent.nodes import (
    node_build_context,
    node_diagnose_root_cause,
    node_extract_alert,
    node_frame_problem,
    node_publish_findings,
)
from src.agent.nodes.investigate.investigate import node_investigate
from src.agent.state import InvestigationState, make_initial_state


def build_graph_pipeline(checkpointer: InMemorySaver | None = None) -> CompiledStateGraph:
    """
    Build the investigation state machine.

    Simplified flow:
        START
        → extract_alert (parallel)
        → build_context (parallel)
        → frame_problem (waits for both, generates statement)
        → investigate      # Determine tools and gather evidence in one go
        → diagnose_root_cause    # Synthesize conclusion with validation
        → publish_findings       # Format outputs
        → END
    """
    graph = StateGraph(InvestigationState)

    # Nodes define the agentic steps in the graph pipeline
    ## Initial parallel nodes
    graph.add_node("extract_alert", node_extract_alert)
    graph.add_node("build_context", node_build_context)

    ## Frame Problem Node (statement generation)
    graph.add_node("frame_problem", node_frame_problem)

    ## Hypothesis Investigation Nodes
    graph.add_node("investigate", node_investigate)
    graph.add_node("diagnose_root_cause", node_diagnose_root_cause)
    graph.add_node("publish_findings", node_publish_findings)

    # Edges define the shape of the graph pipeline
    # Parallel execution: both extract_alert and build_context start from START
    graph.add_edge(START, "extract_alert")
    graph.add_edge(START, "build_context")

    # frame_problem waits for both to complete (LangGraph waits for all incoming edges)
    graph.add_edge("extract_alert", "frame_problem")
    graph.add_edge("build_context", "frame_problem")

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

    # Compile with checkpointer for short-term memory (thread-level persistence)
    # If no checkpointer provided, use in-memory by default
    if checkpointer is None:
        checkpointer = InMemorySaver()

    return graph.compile(checkpointer=checkpointer)


def run_investigation_pipeline(
    alert_name: str,
    affected_table: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
    thread_id: str | None = None,
    checkpointer: InMemorySaver | None = None,
) -> InvestigationState:
    """
    Run the investigation graph.

    Pure function: inputs in, state out. No rendering.

    Args:
        alert_name: Name of the alert
        affected_table: Affected table name
        severity: Alert severity
        raw_alert: Raw alert payload
        thread_id: Optional thread ID for short-term memory persistence.
                   If provided, state will be persisted and can be resumed.
                   If None, each run is independent.
        checkpointer: Optional checkpointer instance. If None, uses InMemorySaver.

    Returns:
        Final investigation state
    """
    graph = build_graph_pipeline(checkpointer=checkpointer)

    initial_state = make_initial_state(
        alert_name,
        affected_table,
        severity,
        raw_alert=raw_alert,
    )

    # Run the graph with optional thread_id for memory persistence
    if thread_id:
        config = {"configurable": {"thread_id": thread_id}}
        final_state = graph.invoke(initial_state, config=config)
    else:
        final_state = graph.invoke(initial_state)

    return final_state
