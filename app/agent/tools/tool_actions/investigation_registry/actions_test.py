from __future__ import annotations

import builtins
import sys
from typing import Any

from app.agent.tools.tool_actions.investigation_registry.actions import (
    get_available_actions,
)


def test_get_available_actions_skips_eks_when_dependency_missing(monkeypatch) -> None:
    original_import = builtins.__import__

    for module_name in (
        "app.agent.tools.tool_actions.eks.eks_cluster_actions",
        "app.agent.tools.tool_actions.eks.eks_workload_actions",
    ):
        sys.modules.pop(module_name, None)

    def _fake_import(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        if name in {
            "app.agent.tools.tool_actions.eks.eks_cluster_actions",
            "app.agent.tools.tool_actions.eks.eks_workload_actions",
        }:
            raise ModuleNotFoundError("No module named 'kubernetes'")
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    actions = get_available_actions()
    action_names = {action.name for action in actions}

    assert "query_datadog_logs" in action_names
    assert "list_eks_clusters" not in action_names


def test_execute_aws_operation_is_not_auto_plannable() -> None:
    actions = get_available_actions()
    action_by_name = {action.name: action for action in actions}

    execute_aws_operation = action_by_name["execute_aws_operation"]

    assert execute_aws_operation.availability_check is not None
    assert (
        execute_aws_operation.availability_check(
            {"aws_metadata": {"service": "rds", "resource_id": "payments-prod"}}
        )
        is False
    )
