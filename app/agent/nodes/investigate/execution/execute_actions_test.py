"""Tests for execute_actions."""

from collections.abc import Callable
from dataclasses import dataclass

from app.agent.nodes.investigate.execution.execute_actions import execute_actions


@dataclass
class DummyAction:
    name: str
    availability_check: Callable[[dict], bool] | None
    parameter_extractor: Callable[[dict], dict]
    function: Callable[..., dict]


def test_execute_actions_success():
    """Returns success when action completes without error."""
    action = DummyAction(
        name="get_success",
        availability_check=None,
        parameter_extractor=lambda _sources: {"value": 123},
        function=lambda value: {"result": value},
    )

    results = execute_actions(["get_success"], [action], {"any": {}})

    assert results["get_success"].success is True
    assert results["get_success"].data["result"] == 123
    assert results["get_success"].error is None


def test_execute_actions_unavailable_action():
    """Returns unavailable error when availability check fails."""
    action = DummyAction(
        name="get_unavailable",
        availability_check=lambda _sources: False,
        parameter_extractor=lambda _sources: {},
        function=lambda: {"result": "should-not-run"},
    )

    results = execute_actions(["get_unavailable"], [action], {"any": {}})

    assert results["get_unavailable"].success is False
    assert (
        results["get_unavailable"].error == "Action not available: required data sources not found"
    )


def test_execute_actions_unknown_action():
    """Returns unknown action error when name is not in available actions."""
    results = execute_actions(["missing_action"], [], {"any": {}})

    assert results["missing_action"].success is False
    assert results["missing_action"].error == "Unknown action: missing_action"
