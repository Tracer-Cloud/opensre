"""
Investigation Graph - Orchestrates the incident resolution workflow.

Architecture:
    Linear deterministic flow:
    1. Planning Phase: Deterministic rules produce plan_sources
    2. Evidence Gathering Phase: Execute plan by calling tools directly
    3. Analysis Phase: Synthesize root cause from collected evidence
    4. Output Phase: Generate reports (Slack, Markdown)

No ReAct loop. Tools are called directly based on the plan.
"""

from langgraph.graph import END, START, StateGraph

from src.agent.domain.state import InvestigationState, make_initial_state

# Nodes (orchestration)
from src.agent.nodes import (
    node_analyze,
    node_gather_evidence,
    node_output,
    node_plan,
)


def build_graph() -> StateGraph:
    """
    Build the investigation state machine.

    Linear flow:
        START -> plan -> gather_evidence -> analyze -> output -> END

    No ReAct loop. Tools are called directly based on the plan.
    """
    graph = StateGraph(InvestigationState)

    # Add nodes
    graph.add_node("plan", node_plan)
    graph.add_node("gather_evidence", node_gather_evidence)
    graph.add_node("analyze", node_analyze)
    graph.add_node("output", node_output)

    # Linear flow
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "gather_evidence")
    graph.add_edge("gather_evidence", "analyze")
    graph.add_edge("analyze", "output")
    graph.add_edge("output", END)

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

