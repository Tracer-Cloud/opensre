"""Tests for tool routing metadata and fallback selection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.nodes.route_tools.route_tools import (
    DEFAULT_FALLBACK_TOOLSETS,
    MAX_FALLBACK_TOOLS,
    MIN_CONFIDENCE_THRESHOLD,
    RoutingResult,
    ToolSelectionResult,
    filter_available_tools,
    route_tools_by_tags,
    route_tools_by_toolset,
    select_fallback_tools,
)
from app.tools.base import BaseTool, ToolMetadata
from app.tools.registered_tool import RegisteredTool


class MockTool:
    """Mock tool for testing routing logic."""

    def __init__(
        self,
        name: str,
        toolset: str = "default",
        tags: list[str] | None = None,
        cost_hint: str = "low",
        source: str = "storage",
    ):
        self.name = name
        self.description = f"Mock {name}"
        self.toolset = toolset
        self.tags = tags or []
        self.cost_hint = cost_hint
        self.source = source
        self.input_schema: dict = {"type": "object", "properties": {}}
        self.use_cases: list = []
        self.requires: list = []
        self.outputs: dict = {}

    def is_available(self, _sources: dict) -> bool:
        return True


class TestToolMetadata:
    """Tests for ToolMetadata routing fields."""

    def test_default_routing_values(self) -> None:
        metadata = ToolMetadata.model_validate(
            {
                "name": "test_tool",
                "description": "A test tool",
                "input_schema": {},
                "source": "storage",
            }
        )
        assert metadata.toolset == "default"
        assert metadata.tags == []
        assert metadata.cost_hint == "low"

    def test_custom_routing_values(self) -> None:
        metadata = ToolMetadata.model_validate(
            {
                "name": "aws_tool",
                "description": "AWS tool",
                "input_schema": {},
                "source": "aws_sdk",
                "toolset": "aws",
                "tags": ["infrastructure", "compute"],
                "cost_hint": "medium",
            }
        )
        assert metadata.toolset == "aws"
        assert metadata.tags == ["infrastructure", "compute"]
        assert metadata.cost_hint == "medium"

    def test_cost_hint_validation_normalizes_case(self) -> None:
        metadata = ToolMetadata.model_validate(
            {
                "name": "test",
                "description": "Test",
                "input_schema": {},
                "source": "storage",
                "cost_hint": "HIGH",
            }
        )
        assert metadata.cost_hint == "high"

    def test_cost_hint_validation_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError, match="cost_hint"):
            ToolMetadata.model_validate(
                {
                    "name": "test",
                    "description": "Test",
                    "input_schema": {},
                    "source": "storage",
                    "cost_hint": "invalid",
                }
            )


class TestBaseToolRoutingMetadata:
    """Tests for BaseTool routing metadata inheritance."""

    def test_base_tool_with_routing_metadata(self) -> None:
        class K8sTool(BaseTool):
            name = "k8s_discovery"
            description = "Discover K8s resources"
            input_schema = {"type": "object", "properties": {}}
            source = "eks"
            toolset = "k8s"
            tags = ["discovery", "infrastructure"]
            cost_hint = "low"
            use_cases = ["Find pods", "List deployments"]

            def run(self, **_kwargs) -> dict:
                return {}

        tool = K8sTool()
        metadata = tool.metadata()

        assert metadata.toolset == "k8s"
        assert metadata.tags == ["discovery", "infrastructure"]
        assert metadata.cost_hint == "low"


class TestRegisteredToolRouting:
    """Tests for RegisteredTool routing metadata."""

    def test_registered_tool_with_routing(self) -> None:
        tool = RegisteredTool(
            name="test",
            description="Test tool",
            input_schema={},
            source="storage",
            run=lambda: {},
            toolset="logs",
            tags=["query", "filter"],
            cost_hint="high",
        )

        assert tool.toolset == "logs"
        assert tool.tags == ["query", "filter"]
        assert tool.cost_hint == "high"

    def test_registered_tool_defaults(self) -> None:
        tool = RegisteredTool(
            name="test",
            description="Test tool",
            input_schema={},
            source="storage",
            run=lambda: {},
        )

        assert tool.toolset == "default"
        assert tool.tags == []
        assert tool.cost_hint == "low"


class TestFallbackSelection:
    """Tests for deterministic fallback selection."""

    def test_fallback_selects_from_default_toolsets(self) -> None:
        tools = [
            MockTool("tool1", toolset="core"),
            MockTool("tool2", toolset="discovery"),
            MockTool("tool3", toolset="logs"),
            MockTool("tool4", toolset="custom"),  # Should be excluded
        ]

        result = select_fallback_tools(tools)

        selected_toolsets = {r.tool.toolset for r in result}
        assert selected_toolsets.issubset(DEFAULT_FALLBACK_TOOLSETS)
        assert "custom" not in selected_toolsets

    def test_fallback_prioritizes_low_cost_first(self) -> None:
        tools = [
            MockTool("expensive", toolset="core", cost_hint="high"),
            MockTool("cheap", toolset="core", cost_hint="low"),
            MockTool("medium", toolset="core", cost_hint="medium"),
        ]

        result = select_fallback_tools(tools)

        # Low cost should be first
        assert result[0].tool.name == "cheap"
        assert result[0].tool.cost_hint == "low"

    def test_fallback_limited_to_max_tools(self) -> None:
        tools = [MockTool(f"tool{i}", toolset="core") for i in range(10)]

        result = select_fallback_tools(tools)

        assert len(result) <= MAX_FALLBACK_TOOLS

    def test_fallback_provides_inclusion_reasons(self) -> None:
        tools = [MockTool("test", toolset="core", cost_hint="low")]

        result = select_fallback_tools(tools)

        assert len(result) == 1
        assert "Fallback selection" in result[0].inclusion_reason
        assert "core" in result[0].inclusion_reason
        assert "low" in result[0].inclusion_reason

    def test_fallback_uses_any_when_no_eligible(self) -> None:
        tools = [
            MockTool("custom1", toolset="custom1"),
            MockTool("custom2", toolset="custom2"),
        ]

        result = select_fallback_tools(tools)

        # Should use some tools since no default toolset matches
        assert len(result) > 0


class TestRoutingByTags:
    """Tests for tag-based tool routing."""

    def test_route_by_tags_finds_matches(self) -> None:
        tools = [
            MockTool("logs", tags=["logs", "query"]),
            MockTool("metrics", tags=["metrics", "query"]),
            MockTool("traces", tags=["traces"]),
        ]

        result = route_tools_by_tags(tools, ["query"])

        assert not result.fallback_used
        assert len(result.selected_tools) == 2
        selected_names = {t.tool.name for t in result.selected_tools}
        assert selected_names == {"logs", "metrics"}

    def test_route_by_tags_uses_fallback_when_no_match(self) -> None:
        tools = [
            MockTool("logs", toolset="logs", tags=["logs"]),
            MockTool("metrics", toolset="core", tags=["metrics"]),
        ]

        result = route_tools_by_tags(tools, ["nonexistent"])

        assert result.fallback_used
        assert result.confidence < MIN_CONFIDENCE_THRESHOLD

    def test_route_by_tags_uses_fallback_when_no_tags_specified(self) -> None:
        tools = [MockTool("tool1", toolset="core")]

        result = route_tools_by_tags(tools, [])

        assert result.fallback_used

    def test_route_by_tags_includes_reasons(self) -> None:
        tools = [MockTool("logs", tags=["logs", "error"])]

        result = route_tools_by_tags(tools, ["logs", "error"])

        assert len(result.selected_tools) == 1
        reason = result.selected_tools[0].inclusion_reason
        assert "Tag match" in reason
        assert "logs" in reason
        assert "error" in reason

    def test_route_by_tags_uses_fallback_when_best_match_below_threshold(self) -> None:
        tools = [
            MockTool("logs", toolset="logs", tags=["logs"]),
            MockTool("metrics", toolset="core", tags=["metrics"]),
        ]

        result = route_tools_by_tags(tools, ["logs", "query"], min_confidence=0.75)

        assert result.fallback_used is True
        assert all(selection.is_fallback for selection in result.selected_tools)
        assert result.confidence == 0.5


class TestRoutingByToolset:
    """Tests for toolset-based routing."""

    def test_route_by_toolset_finds_matches(self) -> None:
        tools = [
            MockTool("aws1", toolset="aws"),
            MockTool("aws2", toolset="aws"),
            MockTool("k8s1", toolset="k8s"),
        ]

        result = route_tools_by_toolset(tools, ["aws"])

        assert not result.fallback_used
        assert len(result.selected_tools) == 2
        for r in result.selected_tools:
            assert r.tool.toolset == "aws"

    def test_route_by_toolset_uses_fallback_when_no_match(self) -> None:
        tools = [
            MockTool("aws", toolset="aws"),
            MockTool("k8s", toolset="k8s"),
        ]

        result = route_tools_by_toolset(tools, ["nonexistent"])

        assert result.fallback_used

    def test_route_by_toolset_case_insensitive(self) -> None:
        tools = [MockTool("test", toolset="AWS")]

        result = route_tools_by_toolset(tools, ["aws"])

        assert not result.fallback_used
        assert len(result.selected_tools) == 1


class TestFilterAvailableTools:
    """Tests for filtering available tools."""

    def test_filters_by_is_available(self) -> None:
        class UnavailableTool(MockTool):
            def is_available(self, _sources: dict) -> bool:
                return False

        available = MockTool("available")
        unavailable = UnavailableTool("unavailable")

        result = filter_available_tools([available, unavailable], {})

        assert len(result) == 1
        assert result[0].name == "available"


class TestRoutingResult:
    """Tests for RoutingResult data structure."""

    def test_to_tool_names(self) -> None:
        tool = MockTool("test")
        result = RoutingResult(
            selected_tools=[ToolSelectionResult(tool=tool, inclusion_reason="test", confidence=1.0)]
        )

        assert result.to_tool_names() == ["test"]

    def test_to_detailed_dict(self) -> None:
        tool = MockTool("test", toolset="logs", tags=["query"], cost_hint="medium")
        result = RoutingResult(
            selected_tools=[
                ToolSelectionResult(
                    tool=tool,
                    inclusion_reason="test reason",
                    confidence=0.9,
                    is_fallback=False,
                )
            ],
            fallback_used=False,
            routing_reason="test",
            confidence=0.9,
        )

        detailed = result.to_detailed_dict()

        assert detailed["fallback_used"] is False
        assert detailed["overall_confidence"] == 0.9
        assert len(detailed["tools"]) == 1
        assert detailed["tools"][0]["name"] == "test"
        assert detailed["tools"][0]["reason"] == "test reason"
        assert detailed["tools"][0]["toolset"] == "logs"
        assert detailed["tools"][0]["cost_hint"] == "medium"


class TestConstants:
    """Tests for routing constants."""

    def test_default_fallback_toolsets(self) -> None:
        assert "core" in DEFAULT_FALLBACK_TOOLSETS
        assert "discovery" in DEFAULT_FALLBACK_TOOLSETS
        assert "logs" in DEFAULT_FALLBACK_TOOLSETS

    def test_min_confidence_threshold(self) -> None:
        assert MIN_CONFIDENCE_THRESHOLD == 0.5

    def test_max_fallback_tools(self) -> None:
        assert MAX_FALLBACK_TOOLS == 3
