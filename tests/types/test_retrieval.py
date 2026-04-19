"""Tests for retrieval controls types (isolated from app dependencies)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.types.retrieval import (
    AggregationSpec,
    FieldSelection,
    FilterCondition,
    RetrievalControls,
    RetrievalIntent,
    TimeBounds,
)


class TestRetrievalIntent:
    """Tests for RetrievalIntent model."""

    def test_empty_retrieval_intent(self) -> None:
        """RetrievalIntent with no fields set is valid."""
        intent = RetrievalIntent()
        assert intent.time_bounds is None
        assert intent.filters is None
        assert intent.limit is None
        assert intent.fields is None
        assert intent.aggregation is None
        assert not intent.has_controls()

    def test_retrieval_intent_with_controls(self) -> None:
        """RetrievalIntent with any control set has_controls returns True."""
        intent = RetrievalIntent(limit=100)
        assert intent.has_controls()
        assert intent.limit == 100

    def test_retrieval_intent_with_time_bounds(self) -> None:
        """RetrievalIntent with time bounds."""
        time_bounds = TimeBounds(
            start_time="2024-01-01T00:00:00Z",
            end_time="2024-01-02T00:00:00Z",
        )
        intent = RetrievalIntent(time_bounds=time_bounds)
        assert intent.has_controls()
        assert intent.time_bounds is not None
        assert intent.time_bounds.start_time == "2024-01-01T00:00:00Z"

    def test_retrieval_intent_with_filters(self) -> None:
        """RetrievalIntent with filter conditions."""
        filters = [
            FilterCondition(field="status", operator="eq", value="error"),
            FilterCondition(field="service", operator="contains", value="api"),
        ]
        intent = RetrievalIntent(filters=filters)
        assert intent.has_controls()
        assert len(intent.filters) == 2
        assert intent.filters[0].field == "status"

    def test_retrieval_intent_with_field_selection(self) -> None:
        """RetrievalIntent with field selection."""
        fields = FieldSelection(include=["timestamp", "message", "level"])
        intent = RetrievalIntent(fields=fields)
        assert intent.has_controls()
        assert intent.fields.include == ["timestamp", "message", "level"]

    def test_retrieval_intent_with_aggregation(self) -> None:
        """RetrievalIntent with aggregation spec."""
        agg = AggregationSpec(
            group_by=["service"],
            function="count",
            time_bucket="5m",
        )
        intent = RetrievalIntent(aggregation=agg)
        assert intent.has_controls()
        assert intent.aggregation.function == "count"

    def test_retrieval_intent_limit_bounds(self) -> None:
        """RetrievalIntent limit must be within bounds."""
        # Valid bounds
        RetrievalIntent(limit=1)
        RetrievalIntent(limit=10000)

        # Invalid bounds
        with pytest.raises(ValidationError):
            RetrievalIntent(limit=0)
        with pytest.raises(ValidationError):
            RetrievalIntent(limit=10001)
        with pytest.raises(ValidationError):
            RetrievalIntent(limit=-1)


class TestTimeBounds:
    """Tests for TimeBounds model."""

    def test_time_bounds_all_fields(self) -> None:
        """TimeBounds with all fields set."""
        bounds = TimeBounds(
            start_time="2024-01-01T00:00:00Z",
            end_time="2024-01-02T00:00:00Z",
            lookback_minutes=60,
        )
        assert bounds.start_time == "2024-01-01T00:00:00Z"
        assert bounds.end_time == "2024-01-02T00:00:00Z"
        assert bounds.lookback_minutes == 60

    def test_time_bounds_partial(self) -> None:
        """TimeBounds with only some fields set."""
        bounds = TimeBounds(lookback_minutes=30)
        assert bounds.start_time is None
        assert bounds.end_time is None
        assert bounds.lookback_minutes == 30

    def test_time_bounds_lookback_bounds(self) -> None:
        """TimeBounds lookback_minutes must be within bounds."""
        # Valid bounds
        TimeBounds(lookback_minutes=1)
        TimeBounds(lookback_minutes=10080)

        # Invalid bounds
        with pytest.raises(ValidationError):
            TimeBounds(lookback_minutes=0)
        with pytest.raises(ValidationError):
            TimeBounds(lookback_minutes=10081)


class TestFilterCondition:
    """Tests for FilterCondition model."""

    def test_filter_condition_operators(self) -> None:
        """FilterCondition supports all valid operators."""
        operators = [
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "contains",
            "startswith",
            "endswith",
            "regex",
        ]
        for op in operators:
            f = FilterCondition(field="test", operator=op, value="x")
            assert f.operator == op

    def test_filter_condition_invalid_operator(self) -> None:
        """FilterCondition rejects invalid operators."""
        with pytest.raises(ValidationError):
            FilterCondition(field="test", operator="invalid", value="x")


class TestFieldSelection:
    """Tests for FieldSelection model."""

    def test_field_selection_include(self) -> None:
        """FieldSelection with include fields."""
        sel = FieldSelection(include=["a", "b", "c"])
        assert sel.include == ["a", "b", "c"]
        assert sel.exclude is None

    def test_field_selection_exclude(self) -> None:
        """FieldSelection with exclude fields."""
        sel = FieldSelection(exclude=["x", "y"])
        assert sel.exclude == ["x", "y"]
        assert sel.include is None

    def test_field_selection_both(self) -> None:
        """FieldSelection with both include and exclude."""
        sel = FieldSelection(include=["a"], exclude=["b"])
        assert sel.include == ["a"]
        assert sel.exclude == ["b"]


class TestAggregationSpec:
    """Tests for AggregationSpec model."""

    def test_aggregation_functions(self) -> None:
        """AggregationSpec supports all valid functions."""
        functions = ["count", "sum", "avg", "min", "max", "p50", "p95", "p99"]
        for fn in functions:
            agg = AggregationSpec(function=fn, field=None if fn == "count" else "duration_ms")
            assert agg.function == fn

    def test_aggregation_invalid_function(self) -> None:
        """AggregationSpec rejects invalid functions."""
        with pytest.raises(ValidationError):
            AggregationSpec(function="invalid")

    def test_non_count_aggregation_requires_field(self) -> None:
        """Non-count aggregation functions require a target field."""
        for function in ("sum", "avg", "min", "max", "p50", "p95", "p99"):
            with pytest.raises(ValidationError, match="'field' is required"):
                AggregationSpec(function=function)

    def test_count_aggregation_does_not_require_field(self) -> None:
        """Count aggregation can omit field."""
        agg = AggregationSpec(function="count")
        assert agg.field is None


class TestRetrievalControls:
    """Tests for RetrievalControls model."""

    def test_default_retrieval_controls(self) -> None:
        """Default RetrievalControls has no controls enabled."""
        controls = RetrievalControls()
        assert not controls.time_bounds
        assert not controls.filters
        assert not controls.limit
        assert not controls.fields
        assert not controls.aggregation
        assert controls.supported == []
        assert not controls.supports_any()

    def test_retrieval_controls_supported(self) -> None:
        """RetrievalControls.supported returns enabled controls."""
        controls = RetrievalControls(
            time_bounds=True,
            limit=True,
            fields=True,
        )
        assert set(controls.supported) == {"time_bounds", "limit", "fields"}
        assert controls.supports_any()

    def test_retrieval_controls_all_enabled(self) -> None:
        """RetrievalControls with all controls enabled."""
        controls = RetrievalControls(
            time_bounds=True,
            filters=True,
            limit=True,
            fields=True,
            aggregation=True,
        )
        assert set(controls.supported) == {
            "time_bounds",
            "filters",
            "limit",
            "fields",
            "aggregation",
        }


class TestToolMetadataRetrieval:
    """Tests for ToolMetadata with retrieval controls (isolated)."""

    def test_tool_metadata_default_retrieval_controls(self) -> None:
        """ToolMetadata defaults to no retrieval controls."""
        from app.tools.base import ToolMetadata

        metadata = ToolMetadata(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object"},
            source="knowledge",
        )
        assert not metadata.retrieval_controls.supports_any()

    def test_tool_metadata_with_retrieval_controls(self) -> None:
        """ToolMetadata with explicit retrieval controls."""
        from app.tools.base import ToolMetadata

        controls = RetrievalControls(time_bounds=True, limit=True)
        metadata = ToolMetadata(
            name="test_tool",
            description="A test tool with controls",
            input_schema={"type": "object"},
            source="knowledge",
            retrieval_controls=controls,
        )
        assert metadata.retrieval_controls.time_bounds
        assert metadata.retrieval_controls.limit
        assert not metadata.retrieval_controls.filters


class TestBaseToolRetrieval:
    """Tests for BaseTool with retrieval controls (isolated)."""

    def test_base_tool_default_retrieval_controls(self) -> None:
        """BaseTool subclasses default to no retrieval controls."""
        from typing import Any, ClassVar

        from app.tools.base import BaseTool
        from app.types.evidence import EvidenceSource

        class SimpleTool(BaseTool):
            name: ClassVar[str] = "simple_tool"
            description: ClassVar[str] = "A simple test tool"
            input_schema: ClassVar[dict[str, Any]] = {"type": "object"}
            source: ClassVar[EvidenceSource] = "knowledge"

            def run(self) -> dict[str, Any]:
                return {"result": "ok"}

        tool = SimpleTool()
        metadata = tool.metadata()
        assert not metadata.retrieval_controls.supports_any()

    def test_base_tool_with_retrieval_controls(self) -> None:
        """BaseTool subclass can declare supported retrieval controls."""
        from typing import Any, ClassVar

        from app.tools.base import BaseTool
        from app.types.evidence import EvidenceSource

        class ControlledTool(BaseTool):
            name: ClassVar[str] = "controlled_tool"
            description: ClassVar[str] = "A tool with controls"
            input_schema: ClassVar[dict[str, Any]] = {"type": "object"}
            source: ClassVar[EvidenceSource] = "knowledge"
            retrieval_controls: ClassVar[RetrievalControls] = RetrievalControls(
                time_bounds=True,
                limit=True,
            )

            def run(self) -> dict[str, Any]:
                return {"result": "ok"}

        tool = ControlledTool()
        metadata = tool.metadata()
        assert metadata.retrieval_controls.time_bounds
        assert metadata.retrieval_controls.limit
        assert set(metadata.retrieval_controls.supported) == {"time_bounds", "limit"}

    def test_base_tool_retrieval_controls_preserved_on_subclass(self) -> None:
        """Retrieval controls are preserved through metadata() call."""
        from typing import Any, ClassVar

        from app.tools.base import BaseTool
        from app.types.evidence import EvidenceSource

        class ParentTool(BaseTool):
            name: ClassVar[str] = "parent"
            description: ClassVar[str] = "Parent"
            input_schema: ClassVar[dict[str, Any]] = {"type": "object"}
            source: ClassVar[EvidenceSource] = "knowledge"
            retrieval_controls: ClassVar[RetrievalControls] = RetrievalControls(
                filters=True,
                fields=True,
            )

            def run(self) -> dict[str, Any]:
                return {}

        # Accessing metadata() should preserve retrieval_controls
        metadata = ParentTool.metadata()
        assert metadata.retrieval_controls.filters
        assert metadata.retrieval_controls.fields

        # The class attribute should also be updated by __init_subclass__
        assert ParentTool.retrieval_controls.filters
        assert ParentTool.retrieval_controls.fields
