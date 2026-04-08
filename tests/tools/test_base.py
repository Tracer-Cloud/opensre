from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.tools.base import BaseTool


def test_base_tool_rejects_blank_name() -> None:
    with pytest.raises(ValidationError, match="name"):
        type(
            "BlankNameTool",
            (BaseTool,),
            {
                "name": "   ",
                "description": "Valid description",
                "input_schema": {"type": "object", "properties": {}},
                "source": "grafana",
                "run": lambda _self, **_kwargs: {},
            },
        )


def test_base_tool_rejects_blank_description() -> None:
    with pytest.raises(ValidationError, match="description"):
        type(
            "BlankDescriptionTool",
            (BaseTool,),
            {
                "name": "valid_tool",
                "description": "   ",
                "input_schema": {"type": "object", "properties": {}},
                "source": "grafana",
                "run": lambda _self, **_kwargs: {},
            },
        )


def test_base_tool_default_routing_metadata() -> None:
    class SimpleTool(BaseTool):
        name = "simple_tool"
        description = "A simple test tool"
        input_schema = {"type": "object", "properties": {}}
        source = "storage"

        def run(self, **_kwargs) -> dict:
            return {}

    tool = SimpleTool()
    metadata = tool.metadata()

    assert metadata.toolset == "default"
    assert metadata.tags == []
    assert metadata.cost_hint == "low"


def test_base_tool_custom_routing_metadata() -> None:
    class RoutingTool(BaseTool):
        name = "aws_discovery"
        description = "AWS discovery tool"
        input_schema = {"type": "object", "properties": {}}
        source = "aws_sdk"
        toolset = "aws"
        tags = ["infrastructure", "discovery", "ec2"]
        cost_hint = "medium"

        def run(self, **_kwargs) -> dict:
            return {}

    tool = RoutingTool()
    metadata = tool.metadata()

    assert metadata.toolset == "aws"
    assert metadata.tags == ["infrastructure", "discovery", "ec2"]
    assert metadata.cost_hint == "medium"


def test_base_tool_classvars_inherited_correctly() -> None:
    class K8sTool(BaseTool):
        name = "k8s_pods"
        description = "List Kubernetes pods"
        input_schema = {"type": "object", "properties": {}}
        source = "eks"
        toolset = "k8s"
        tags = ["discovery", "pods"]
        cost_hint = "low"

        def run(self, **_kwargs) -> dict:
            return {}

    # Check class-level attributes
    assert K8sTool.toolset == "k8s"
    assert K8sTool.tags == ["discovery", "pods"]
    assert K8sTool.cost_hint == "low"

    # Check instance-level metadata
    tool = K8sTool()
    metadata = tool.metadata()
    assert metadata.toolset == "k8s"
    assert metadata.tags == ["discovery", "pods"]
    assert metadata.cost_hint == "low"


def test_base_tool_cost_hint_validation() -> None:
    class HighCostTool(BaseTool):
        name = "expensive_tool"
        description = "Expensive operation"
        input_schema = {"type": "object", "properties": {}}
        source = "batch"
        cost_hint = "HIGH"  # Uppercase - should be normalized

        def run(self, **_kwargs) -> dict:
            return {}

    tool = HighCostTool()
    metadata = tool.metadata()
    assert metadata.cost_hint == "high"  # Normalized to lowercase
