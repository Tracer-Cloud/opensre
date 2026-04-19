"""Retrieval controls for structured evidence slicing.

Defines a shared contract between planning and tool execution that allows
plans to request specific slices of evidence (time bounds, filters, limits,
field selection, aggregation) and tools to declare which controls they support.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TimeBounds(BaseModel):
    """Time range for evidence retrieval.

    Supports both absolute ISO timestamps and relative durations.
    """

    model_config = ConfigDict(extra="forbid")

    start_time: str | None = Field(
        default=None,
        description="Start of time range (ISO 8601 timestamp or relative like '-1h')",
    )
    end_time: str | None = Field(
        default=None,
        description="End of time range (ISO 8601 timestamp or relative like 'now')",
    )
    lookback_minutes: int | None = Field(
        default=None,
        description="Alternative to start_time: look back N minutes from end_time",
        ge=1,
        le=10080,  # Max 1 week in minutes
    )


class FilterCondition(BaseModel):
    """Single filter condition for evidence retrieval.

    Supports equality, pattern matching, and range comparisons.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="Field name to filter on")
    operator: Literal[
        "eq", "ne", "gt", "gte", "lt", "lte", "contains", "startswith", "endswith", "regex"
    ] = Field(
        default="eq",
        description="Comparison operator",
    )
    value: Any = Field(description="Value to compare against")


class FieldSelection(BaseModel):
    """Field selection for targeted evidence retrieval.

    Reduces payload size and improves signal-to-noise.
    """

    model_config = ConfigDict(extra="forbid")

    include: list[str] | None = Field(
        default=None,
        description="Fields to include in response (None = all fields)",
    )
    exclude: list[str] | None = Field(
        default=None,
        description="Fields to exclude from response",
    )


class AggregationSpec(BaseModel):
    """Aggregation specification for summarized evidence.

    Tools that support aggregation return summarized views instead of raw events.
    """

    model_config = ConfigDict(extra="forbid")

    group_by: list[str] | None = Field(
        default=None,
        description="Fields to group by for aggregation",
    )
    function: Literal["count", "sum", "avg", "min", "max", "p50", "p95", "p99"] = Field(
        default="count",
        description="Aggregation function to apply",
    )
    field: str | None = Field(
        default=None,
        description="Field to aggregate (required for sum, avg, min, max, percentiles)",
    )
    time_bucket: str | None = Field(
        default=None,
        description="Time bucketing for time-series aggregation (e.g., '1m', '5m', '1h')",
    )

    @model_validator(mode="after")
    def field_required_for_non_count(self) -> AggregationSpec:
        """Require target field for aggregation functions that operate on a value."""
        field_required_functions = {"sum", "avg", "min", "max", "p50", "p95", "p99"}
        if self.function in field_required_functions and self.field is None:
            raise ValueError(f"'field' is required when function is '{self.function}'")
        return self


class RetrievalIntent(BaseModel):
    """Structured retrieval intent for evidence gathering.

    Allows plans to request specific slices of evidence, improving
    signal-to-noise and reducing token consumption.

    Backward compatibility: all fields are optional. Tools ignore
    unsupported controls gracefully.
    """

    model_config = ConfigDict(extra="forbid")

    time_bounds: TimeBounds | None = Field(
        default=None,
        description="Time range for evidence retrieval",
    )
    filters: list[FilterCondition] | None = Field(
        default=None,
        description="Filter conditions to narrow results",
    )
    limit: int | None = Field(
        default=None,
        description="Maximum number of results to return",
        ge=1,
        le=10000,
    )
    fields: FieldSelection | None = Field(
        default=None,
        description="Field selection to reduce payload size",
    )
    aggregation: AggregationSpec | None = Field(
        default=None,
        description="Aggregation specification for summarized data",
    )

    def has_controls(self) -> bool:
        """Check if any retrieval controls are set."""
        return any(
            [
                self.time_bounds is not None,
                self.filters is not None,
                self.limit is not None,
                self.fields is not None,
                self.aggregation is not None,
            ]
        )


class RetrievalControls(BaseModel):
    """Controls supported by a tool.

    Tools declare which retrieval controls they support so planners
    can make informed decisions about where to send structured queries.

    This is declarative metadata consumed by the investigation registry.
    """

    model_config = ConfigDict(extra="forbid")

    time_bounds: bool = Field(
        default=False,
        description="Tool supports time-bounded queries",
    )
    filters: bool = Field(
        default=False,
        description="Tool supports filter conditions",
    )
    limit: bool = Field(
        default=False,
        description="Tool supports result limiting",
    )
    fields: bool = Field(
        default=False,
        description="Tool supports field selection",
    )
    aggregation: bool = Field(
        default=False,
        description="Tool supports aggregation/summarization",
    )

    @property
    def supported(self) -> list[str]:
        """Return list of supported control names."""
        controls = []
        if self.time_bounds:
            controls.append("time_bounds")
        if self.filters:
            controls.append("filters")
        if self.limit:
            controls.append("limit")
        if self.fields:
            controls.append("fields")
        if self.aggregation:
            controls.append("aggregation")
        return controls

    def supports_any(self) -> bool:
        """Check if any controls are supported."""
        return bool(self.supported)


# Type alias for plan-level retrieval controls mapping
# Maps action name to the retrieval intent for that action
RetrievalControlsMap = dict[str, RetrievalIntent]
