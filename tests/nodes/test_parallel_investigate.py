"""Tests for parallel investigate hypothesis routing and merging."""

import pytest
from langgraph.constants import Send

from app.nodes.investigate.execution.execute_actions import ActionExecutionResult
from app.nodes.investigate.merge import merge_hypothesis_results
from app.nodes.investigate.parallel import node_investigate_hypothesis
from app.pipeline.routing import distribute_hypotheses
from app.state.factory import make_initial_state


def test_distribute_hypotheses_with_actions():
    """Test that distribute_hypotheses routes to parallel branches."""
    state = make_initial_state("test", "test", "low")
    state["planned_actions"] = ["query_grafana_logs", "query_datadog_all"]
    state["available_sources"] = {"grafana": {"service_name": "test"}}

    routes = distribute_hypotheses(state)

    assert len(routes) == 2
    for route in routes:
        assert isinstance(route, Send)
        assert route.node == "investigate_hypothesis"
        assert "action_to_run" in route.arg
        assert "available_sources" in route.arg


def test_distribute_hypotheses_empty():
    """Test routing when no actions are planned."""
    state = make_initial_state("test", "test", "low")
    state["planned_actions"] = []

    routes = distribute_hypotheses(state)

    assert len(routes) == 1
    assert routes[0] == "merge_hypothesis_results"


def test_node_investigate_hypothesis_empty():
    """Test parallel node with empty action."""
    state = make_initial_state("test", "test", "low")
    state["action_to_run"] = ""

    result = node_investigate_hypothesis(state)
    assert result == {"hypothesis_results": []}


def test_node_investigate_hypothesis_unknown_action():
    """Test parallel node handles missing registry actions safely."""
    state = make_initial_state("test", "test", "low")
    state["action_to_run"] = "non_existent_action_123"

    result = node_investigate_hypothesis(state)
    assert result == {"hypothesis_results": []}
