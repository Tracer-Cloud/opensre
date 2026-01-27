"""Investigation Graph - Orchestrates the incident resolution workflow."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    node_build_context,
    node_diagnose_root_cause,
    node_extract_alert,
    node_frame_problem,
    node_publish_findings,
)
from app.agent.nodes.investigate.investigate_node import node_investigate
from app.agent.routing import should_continue_investigation
from app.agent.state import InvestigationState, make_initial_state


def build_graph(config: Any | None = None) -> Any:
    """
    Build and compile the investigation graph.

    Flow:
        START
        → extract_alert
        → build_context
        → frame_problem (waits for both)
        → investigate
        → diagnose_root_cause
        → investigate (if needed) or publish_findings
        → END

    Args:
        config: Optional config dict passed by LangGraph runtime.

    Returns:
        Compiled graph ready for execution
    """
    _ = config
    graph = StateGraph(InvestigationState)

    graph.add_node("extract_alert", node_extract_alert)
    graph.add_node("build_context", node_build_context)
    graph.add_node("frame_problem", node_frame_problem)
    graph.add_node("investigate", node_investigate)
    graph.add_node("diagnose_root_cause", node_diagnose_root_cause)
    graph.add_node("publish_findings", node_publish_findings)

    graph.add_edge(START, "extract_alert")
    graph.add_edge(START, "build_context")
    graph.add_edge("extract_alert", "frame_problem")
    graph.add_edge("build_context", "frame_problem")
    graph.add_edge("frame_problem", "investigate")
    graph.add_edge("investigate", "diagnose_root_cause")

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


def resolve_checkpointer_config(
    thread_id: str | None, checkpointer: Any | None
) -> tuple[Any, dict[str, Any]]:
    """
    Resolve checkpointer and config for graph execution.

    Args:
        thread_id: Optional thread ID for state persistence
        checkpointer: Optional checkpointer instance

    Returns:
        Tuple of (compiled_graph, config_dict)
    """
    _ = checkpointer

    if thread_id:
        compiled_graph = build_graph()
        config = {"configurable": {"thread_id": thread_id}}
    else:
        compiled_graph = build_graph()
        config = {}

    return compiled_graph, config


def run_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
    thread_id: str | None = None,
    checkpointer: Any | None = None,
) -> InvestigationState:
    """
    Run the investigation graph.

    Pure function: inputs in, state out. No rendering.

    Args:
        alert_name: Name of the alert
        pipeline_name: Affected table name
        severity: Alert severity
        raw_alert: Raw alert payload
        thread_id: Optional thread ID for short-term memory persistence
        checkpointer: Optional checkpointer instance

    Returns:
        Final investigation state
    """
    compiled_graph, config = resolve_checkpointer_config(thread_id, checkpointer)

    initial_state = make_initial_state(
        alert_name,
        pipeline_name,
        severity,
        raw_alert=raw_alert,
    )

    return compiled_graph.invoke(initial_state, config=config)
